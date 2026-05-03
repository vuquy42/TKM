#!/usr/bin/env python3
"""
common.py - Lớp Router, hàm chung, sysctl, FRR helpers cho dự án Metro Ethernet MPLS.

Tất cả module khác import từ đây để đảm bảo nhất quán.
"""

import os
import subprocess
import time
import datetime

from mininet.node import Node
from mininet.log import info, error

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────
FRR_ZEBRA   = '/usr/lib/frr/zebra'
FRR_OSPFD   = '/usr/lib/frr/ospfd'
FRR_LDPD    = '/usr/lib/frr/ldpd'
FRR_USER    = 'frr'
FRR_GROUP   = 'frr'

MPLS_PLATFORM_LABELS = 100000   # đủ lớn cho LDP label pool
MTU_BACKBONE         = 1512     # tránh drop khi gói MPLS/VPLS mang thêm header
OSPF_WAIT_SEC        = 45       # giây chờ OSPF hội tụ sau khi bật
LDP_WAIT_SEC         = 15       # giây chờ LDP thiết lập LSP sau OSPF


# ──────────────────────────────────────────────────────────────────────────────
# BASE ROUTER CLASS  (CE / PE / P)
# ──────────────────────────────────────────────────────────────────────────────
class LinuxRouter(Node):
    """
    Node Linux hoạt động như Router: bật ip_forward, cài sẵn sysctl MPLS.
    FRR daemon (zebra, ospfd, ldpd) được khởi động riêng bởi hàm start_frr_*
    để tránh race condition khi dựng topo.
    """

    def config(self, **params):
        super().config(**params)
        # ip forwarding – bắt buộc
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
        # MPLS kernel – bắt buộc trước khi gán nhãn
        self.cmd(f'sysctl -w net.mpls.platform_labels={MPLS_PLATFORM_LABELS}')
        # ECMP multipath – cần cho CN3 leaf-spine và cả backbone
        self.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
        # Tắt rp_filter để tránh drop gói asymmetric
        self.cmd('for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > $f; done')
        # Tạo thư mục cấu hình FRR riêng cho từng node
        self.cmd(f'mkdir -p /tmp/{self.name} && chmod 777 /tmp/{self.name}')

    def terminate(self):
        # Dọn tiến trình FRR khi tắt
        self.cmd(f'kill $(cat /tmp/{self.name}/zebra.pid 2>/dev/null) 2>/dev/null')
        self.cmd(f'kill $(cat /tmp/{self.name}/ospfd.pid 2>/dev/null) 2>/dev/null')
        self.cmd(f'kill $(cat /tmp/{self.name}/ldpd.pid 2>/dev/null) 2>/dev/null')
        super().terminate()


# ──────────────────────────────────────────────────────────────────────────────
# MODPROBE MPLS KERNEL MODULES
# ──────────────────────────────────────────────────────────────────────────────
def load_mpls_modules():
    """Nạp module kernel MPLS. Gọi TRƯỚC khi dựng topo."""
    info('*** Loading MPLS kernel modules...\n')
    for mod in ['mpls_router', 'mpls_iptunnel']:
        ret = subprocess.run(['modprobe', mod], capture_output=True)
        if ret.returncode != 0:
            error(f'[WARN] modprobe {mod} failed (maybe already loaded): {ret.stderr.decode()}\n')
        else:
            info(f'    modprobe {mod} OK\n')


# ──────────────────────────────────────────────────────────────────────────────
# MININET CLEANUP
# ──────────────────────────────────────────────────────────────────────────────
def cleanup_mininet():
    """Chạy mn -c và kill các tiến trình FRR còn thừa."""
    info('*** Cleaning up previous Mininet state...\n')
    subprocess.run(['sudo', 'mn', '-c'], capture_output=True, timeout=30)
    subprocess.run(['sudo', 'killall', '-9', 'zebra', 'ospfd', 'ldpd'],
                   capture_output=True)
    time.sleep(1)


# ──────────────────────────────────────────────────────────────────────────────
# FRR DAEMON HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def _write_base_conf(node_name: str, extra: str = '') -> str:
    """Tạo file cấu hình zebra.conf cơ bản và trả về đường dẫn conf_dir."""
    conf_dir = f'/tmp/{node_name}'
    os.makedirs(conf_dir, exist_ok=True)
    base = (
        f'hostname {node_name}\n'
        'log stdout\n'
        'service advanced-vty\n'
        '!\n'
        'line vty\n'
        ' no login\n'
        '!\n'
    ) + extra
    path = f'{conf_dir}/zebra.conf'
    with open(path, 'w') as f:
        f.write(base)
    # vtysh.conf: tìm đúng socket trong conf_dir
    vtysh_conf = f'{conf_dir}/vtysh.conf'
    with open(vtysh_conf, 'w') as f:
        f.write(f'service integrated-vtysh-config\nhostname {node_name}\n')
    # Cấp quyền cho frr nếu user tồn tại
    try:
        subprocess.run(['chown', '-R', f'{FRR_USER}:{FRR_GROUP}', conf_dir],
                       capture_output=True)
    except Exception:
        pass  # Bỏ qua nếu user frr không tồn tại
    return conf_dir


def start_frr_zebra(node):
    """
    Khởi động zebra daemon trong network namespace của node.
    Dùng node.cmd() để chạy TRONG netns (giống Mininet thiết kế).
    """
    conf_dir = _write_base_conf(node.name)
    node.cmd(
        f'{FRR_ZEBRA} -d -u {FRR_USER} -g {FRR_GROUP} -A 127.0.0.1 '
        f'-f {conf_dir}/zebra.conf -i {conf_dir}/zebra.pid '
        f'> {conf_dir}/zebra.log 2>&1 || '
        # Fallback: chạy không có -u/-g nếu user frr không tồn tại
        f'{FRR_ZEBRA} -d -A 127.0.0.1 '
        f'-f {conf_dir}/zebra.conf -i {conf_dir}/zebra.pid '
        f'>> {conf_dir}/zebra.log 2>&1'
    )
    time.sleep(0.5)


def start_frr_ospfd(node):
    """Khởi động ospfd daemon trong netns của node."""
    conf_dir = f'/tmp/{node.name}'
    ospf_conf = f'{conf_dir}/ospfd.conf'
    with open(ospf_conf, 'w') as f:
        f.write(f'hostname {node.name}\nlog stdout\nline vty\n no login\n!\n')
    try:
        subprocess.run(['chown', f'{FRR_USER}:{FRR_GROUP}', ospf_conf],
                       capture_output=True)
    except Exception:
        pass
    node.cmd(
        f'{FRR_OSPFD} -d -u {FRR_USER} -g {FRR_GROUP} -A 127.0.0.1 '
        f'-f {ospf_conf} -i {conf_dir}/ospfd.pid '
        f'> {conf_dir}/ospfd.log 2>&1 || '
        f'{FRR_OSPFD} -d -A 127.0.0.1 '
        f'-f {ospf_conf} -i {conf_dir}/ospfd.pid '
        f'>> {conf_dir}/ospfd.log 2>&1'
    )
    time.sleep(0.5)


def start_frr_ldpd(node):
    """Khởi động ldpd daemon trong netns của node."""
    conf_dir = f'/tmp/{node.name}'
    ldp_conf = f'{conf_dir}/ldpd.conf'
    with open(ldp_conf, 'w') as f:
        f.write(f'hostname {node.name}\nlog stdout\nline vty\n no login\n!\n')
    try:
        subprocess.run(['chown', f'{FRR_USER}:{FRR_GROUP}', ldp_conf],
                       capture_output=True)
    except Exception:
        pass
    node.cmd(
        f'{FRR_LDPD} -d -u {FRR_USER} -g {FRR_GROUP} -A 127.0.0.1 '
        f'-f {ldp_conf} -i {conf_dir}/ldpd.pid '
        f'> {conf_dir}/ldpd.log 2>&1 || '
        f'{FRR_LDPD} -d -A 127.0.0.1 '
        f'-f {ldp_conf} -i {conf_dir}/ldpd.pid '
        f'>> {conf_dir}/ldpd.log 2>&1'
    )
    time.sleep(0.5)


def vtysh_cmd(node, commands: str) -> str:
    """
    Gửi lệnh cấu hình vào FRR vtysh của node (chạy TRONG netns).

    Cách hoạt động:
    - node.cmd() tự động chạy lệnh trong network namespace của node.
    - vtysh connect đến socket của các daemon FRR đang chạy trong cùng netns.
    - Không cần --vty_socket vì daemon và vtysh cùng namespace.

    commands: block lệnh Cisco-style (không cần 'enable', 'conf t' -- tự thêm).
    """
    conf_dir  = f'/tmp/{node.name}'
    # Build full command block
    lines     = ['enable', 'configure terminal'] + commands.strip().split('\n') + ['end', 'write memory']
    cmd_block = '\n'.join(lines)
    # Dùng heredoc để tránh vấn đề escape với ký tự đặc biệt
    result = node.cmd(
        f'printf "{cmd_block}\\n" | '
        f'VTYSH_PAGER=cat vtysh 2>&1'
    )
    return result


def vtysh_exec(node, cmd_str: str) -> str:
    """
    Chạy lệnh show qua vtysh (không cần configure terminal).
    Ví dụ: show ip ospf neighbor, show mpls ldp neighbor.
    """
    return node.cmd(f'VTYSH_PAGER=cat vtysh -c "{cmd_str}" 2>&1')


# ──────────────────────────────────────────────────────────────────────────────
# INTERFACE HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def set_ip(node, intf: str, ip_cidr: str):
    """Gán IP cho interface, flush IP cũ trước."""
    node.cmd(f'ip link set {intf} up')
    node.cmd(f'ip addr flush dev {intf}')
    node.cmd(f'ip addr add {ip_cidr} dev {intf}')


def add_loopback(node, ip_cidr: str, lo_name: str = 'lo'):
    """Thêm địa chỉ loopback (dùng làm router-id cho OSPF/LDP)."""
    node.cmd(f'ip link set {lo_name} up')
    node.cmd(f'ip addr add {ip_cidr} dev {lo_name} 2>/dev/null || true')


def set_mtu(node, intf: str, mtu: int = MTU_BACKBONE):
    """Tăng MTU tránh drop khi đóng gói MPLS."""
    node.cmd(f'ip link set {intf} mtu {mtu}')


def enable_mpls_on_intf(node, intf: str):
    """Bật MPLS input trên interface (cần cho LDP forwarding)."""
    node.cmd(f'sysctl -w net.mpls.conf.{intf}.input=1')


# ──────────────────────────────────────────────────────────────────────────────
# BRIDGE / VPLS HELPERS  (L2 over MPLS fallback)
# ──────────────────────────────────────────────────────────────────────────────
def create_linux_bridge(node, br_name: str, interfaces: list):
    """
    Tạo Linux bridge (dùng làm bridge-domain cho VPLS fallback).
    FRR trong Mininet thường không có full VPLS YANG support,
    nên dùng Linux bridge + GRE/VXLAN tunnel làm Metro Ethernet giả lập.
    """
    node.cmd(f'ip link add name {br_name} type bridge 2>/dev/null || true')
    node.cmd(f'ip link set {br_name} up')
    for intf in interfaces:
        node.cmd(f'ip link set {intf} master {br_name}')
        node.cmd(f'ip link set {intf} up')


def create_gre_tunnel(node, tun_name: str, local_ip: str, remote_ip: str,
                      key: int = 100):
    """
    Tạo GRE tunnel point-to-point để mô phỏng pseudowire/VPLS.

    FALLBACK NOTE: FRRouting trong Mininet không hỗ trợ đầy đủ lệnh VPLS
    (l2vpn evpn / pseudowire-class / xconnect). Thay vào đó, ta dùng
    GRE key tunnel trên Linux để mô phỏng dịch vụ L2 Metro Ethernet
    xuyên backbone MPLS. Gói tin vẫn đi qua MPLS LSP (LDP-assigned label)
    vì GRE endpoint dùng địa chỉ loopback của PE, và các loopback này
    chỉ reachable qua MPLS/OSPF underlay.
    """
    node.cmd(
        f'ip tunnel add {tun_name} mode gre local {local_ip} '
        f'remote {remote_ip} key {key} 2>/dev/null || true'
    )
    node.cmd(f'ip link set {tun_name} up mtu {MTU_BACKBONE}')


# ──────────────────────────────────────────────────────────────────────────────
# LOGGING UTILITY
# ──────────────────────────────────────────────────────────────────────────────
def log_msg(msg: str, log_file: str = None):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    info(line + '\n')
    if log_file:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
