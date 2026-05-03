#!/usr/bin/env python3
"""
backbone.py - Dựng và cấu hình backbone ISP: P1-P4, PE1-PE3.
Trình tự:
  1. add_backbone_nodes()  – thêm node vào net
  2. add_backbone_links()  – nối dây P-P, P-PE
  3. configure_backbone()  – gán IP, loopback, MTU, MPLS
  4. start_backbone_frr()  – bật zebra + ospfd + ldpd
  5. configure_ospf()      – inject vào vtysh
  6. configure_ldp()       – inject vào vtysh
  7. configure_vpls_gre()  – GRE pseudowire fallback (Metro Ethernet)
"""

import time
from mininet.log import info

from common import (LinuxRouter, set_ip, add_loopback, set_mtu,
                    enable_mpls_on_intf, start_frr_zebra, start_frr_ospfd,
                    start_frr_ldpd, vtysh_cmd, vtysh_exec,
                    create_gre_tunnel, create_linux_bridge,
                    MTU_BACKBONE, OSPF_WAIT_SEC, LDP_WAIT_SEC)
from addressing import (
    LOOPBACK, ROUTER_ID,
    LINK_P1_P2, LINK_P1_P3, LINK_P2_P4, LINK_P3_P4,
    LINK_P1_P4, LINK_P2_P3,
    LINK_P1_PE1, LINK_P3_PE2, LINK_P4_PE2,
    LINK_P2_PE3, LINK_P4_PE3,
    LINK_PE1_CE1, LINK_PE2_CE2, LINK_PE3_CE3,
    VPLS_KEY_CN1_CN3, strip_prefix
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. ADD NODES
# ──────────────────────────────────────────────────────────────────────────────
def add_backbone_nodes(net):
    """Thêm 4 router P và 3 router PE vào net."""
    info('*** Adding backbone nodes (P1-P4, PE1-PE3)...\n')
    routers = {}
    for name in ['P1', 'P2', 'P3', 'P4', 'PE1', 'PE2', 'PE3']:
        r = net.addHost(name, cls=LinuxRouter, ip=None)
        routers[name] = r
    return routers


# ──────────────────────────────────────────────────────────────────────────────
# 2. ADD LINKS
# ──────────────────────────────────────────────────────────────────────────────
def add_backbone_links(net):
    """
    Nối dây backbone.
    Trả về dict link_map để biết eth index cho từng liên kết.
    Mininet tự tăng eth index theo thứ tự addLink.
    """
    info('*** Adding backbone links (P-P, P-PE)...\n')
    # P-P links
    net.addLink('P1', 'P2')   # P1-eth0 / P2-eth0
    net.addLink('P1', 'P3')   # P1-eth1 / P3-eth0
    net.addLink('P2', 'P4')   # P2-eth1 / P4-eth0
    net.addLink('P3', 'P4')   # P3-eth1 / P4-eth1
    net.addLink('P1', 'P4')   # P1-eth2 / P4-eth2
    net.addLink('P2', 'P3')   # P2-eth2 / P3-eth2

    # P-PE links
    net.addLink('P1', 'PE1')  # P1-eth3 / PE1-eth0
    net.addLink('P3', 'PE2')  # P3-eth3 / PE2-eth0
    net.addLink('P4', 'PE2')  # P4-eth3 / PE2-eth1
    net.addLink('P2', 'PE3')  # P2-eth3 / PE3-eth0
    net.addLink('P4', 'PE3')  # P4-eth4 / PE3-eth1


# ──────────────────────────────────────────────────────────────────────────────
# 3. CONFIGURE IP + MPLS
# ──────────────────────────────────────────────────────────────────────────────
def configure_backbone(net):
    """
    Gán IP cho tất cả interface backbone, loopback, MTU, enable MPLS input.
    Thứ tự eth phải khớp với thứ tự addLink ở trên.
    """
    info('*** Configuring backbone IPs and MPLS...\n')

    P1  = net['P1'];  P2  = net['P2']
    P3  = net['P3'];  P4  = net['P4']
    PE1 = net['PE1']; PE2 = net['PE2']; PE3 = net['PE3']

    # ── Loopback ──────────────────────────────────────────────────────────────
    for node in [P1, P2, P3, P4, PE1, PE2, PE3]:
        add_loopback(node, LOOPBACK[node.name])

    # ── P1 ────────────────────────────────────────────────────────────────────
    set_ip(P1, 'P1-eth0', LINK_P1_P2[0]);  set_mtu(P1, 'P1-eth0')
    set_ip(P1, 'P1-eth1', LINK_P1_P3[0]);  set_mtu(P1, 'P1-eth1')
    set_ip(P1, 'P1-eth2', LINK_P1_P4[0]);  set_mtu(P1, 'P1-eth2')
    set_ip(P1, 'P1-eth3', LINK_P1_PE1[0]); set_mtu(P1, 'P1-eth3')

    # ── P2 ────────────────────────────────────────────────────────────────────
    set_ip(P2, 'P2-eth0', LINK_P1_P2[1]);  set_mtu(P2, 'P2-eth0')
    set_ip(P2, 'P2-eth1', LINK_P2_P4[0]);  set_mtu(P2, 'P2-eth1')
    set_ip(P2, 'P2-eth2', LINK_P2_P3[0]);  set_mtu(P2, 'P2-eth2')
    set_ip(P2, 'P2-eth3', LINK_P2_PE3[0]); set_mtu(P2, 'P2-eth3')

    # ── P3 ────────────────────────────────────────────────────────────────────
    set_ip(P3, 'P3-eth0', LINK_P1_P3[1]);  set_mtu(P3, 'P3-eth0')
    set_ip(P3, 'P3-eth1', LINK_P3_P4[0]);  set_mtu(P3, 'P3-eth1')
    set_ip(P3, 'P3-eth2', LINK_P2_P3[1]);  set_mtu(P3, 'P3-eth2')
    set_ip(P3, 'P3-eth3', LINK_P3_PE2[0]); set_mtu(P3, 'P3-eth3')

    # ── P4 ────────────────────────────────────────────────────────────────────
    set_ip(P4, 'P4-eth0', LINK_P2_P4[1]);  set_mtu(P4, 'P4-eth0')
    set_ip(P4, 'P4-eth1', LINK_P3_P4[1]);  set_mtu(P4, 'P4-eth1')
    set_ip(P4, 'P4-eth2', LINK_P1_P4[1]);  set_mtu(P4, 'P4-eth2')
    set_ip(P4, 'P4-eth3', LINK_P4_PE2[0]); set_mtu(P4, 'P4-eth3')
    set_ip(P4, 'P4-eth4', LINK_P4_PE3[0]); set_mtu(P4, 'P4-eth4')

    # ── PE1 ───────────────────────────────────────────────────────────────────
    set_ip(PE1, 'PE1-eth0', LINK_P1_PE1[1]); set_mtu(PE1, 'PE1-eth0')
    # PE1-eth1 nối CE1 – cấu hình ở topology.py sau khi CE1 được thêm

    # ── PE2 ───────────────────────────────────────────────────────────────────
    set_ip(PE2, 'PE2-eth0', LINK_P3_PE2[1]); set_mtu(PE2, 'PE2-eth0')
    set_ip(PE2, 'PE2-eth1', LINK_P4_PE2[1]); set_mtu(PE2, 'PE2-eth1')
    # PE2-eth2 nối CE2

    # ── PE3 ───────────────────────────────────────────────────────────────────
    set_ip(PE3, 'PE3-eth0', LINK_P2_PE3[1]); set_mtu(PE3, 'PE3-eth0')
    set_ip(PE3, 'PE3-eth1', LINK_P4_PE3[1]); set_mtu(PE3, 'PE3-eth1')
    # PE3-eth2 nối CE3

    # ── Enable MPLS input trên tất cả interface backbone ─────────────────────
    _enable_mpls_all(net)


def _enable_mpls_all(net):
    """
    Bật MPLS input trên các interface backbone P và PE.
    CHỈ bật cho các interface ĐÃ TỒN TẠI lúc này (P-P và P-PE links).
    Interface PE-CE (PE1-eth1, PE2-eth2, PE3-eth2) sẽ được bật riêng
    bởi enable_mpls_pe_ce() sau khi branch links được addLink().
    """
    mpls_map = {
        'P1':  ['P1-eth0', 'P1-eth1', 'P1-eth2', 'P1-eth3'],
        'P2':  ['P2-eth0', 'P2-eth1', 'P2-eth2', 'P2-eth3'],
        'P3':  ['P3-eth0', 'P3-eth1', 'P3-eth2', 'P3-eth3'],
        'P4':  ['P4-eth0', 'P4-eth1', 'P4-eth2', 'P4-eth3', 'P4-eth4'],
        # PE: chỉ backbone-facing (eth0, eth1) -- KHÔNG có eth2 (CE-facing) vì chưa tồn tại
        'PE1': ['PE1-eth0'],
        'PE2': ['PE2-eth0', 'PE2-eth1'],
        'PE3': ['PE3-eth0', 'PE3-eth1'],
    }
    for node_name, intfs in mpls_map.items():
        node = net[node_name]
        for intf in intfs:
            enable_mpls_on_intf(node, intf)


def enable_mpls_pe_ce(net):
    """
    Bật MPLS input trên các interface PE phía CE.
    Gọi HÀM NÀY sau khi tất cả branch links đã được addLink() và net.start().
    PE1-eth1 (->CE1), PE2-eth2 (->CE2), PE3-eth2 (->CE3).
    """
    pe_ce_map = {
        'PE1': ['PE1-eth1'],
        'PE2': ['PE2-eth2'],
        'PE3': ['PE3-eth2'],
    }
    for node_name, intfs in pe_ce_map.items():
        node = net[node_name]
        for intf in intfs:
            enable_mpls_on_intf(node, intf)


# ──────────────────────────────────────────────────────────────────────────────
# 4. START FRR DAEMONS
# ──────────────────────────────────────────────────────────────────────────────
def start_backbone_frr(net):
    """Khởi động zebra + ospfd + ldpd trên tất cả router backbone."""
    info('*** Starting FRR daemons on backbone routers...\n')
    for name in ['P1', 'P2', 'P3', 'P4', 'PE1', 'PE2', 'PE3']:
        node = net[name]
        start_frr_zebra(node)
        start_frr_ospfd(node)
        start_frr_ldpd(node)
    time.sleep(2)   # Cho daemon khởi động xong


# ──────────────────────────────────────────────────────────────────────────────
# 5. CONFIGURE OSPF (Underlay)
# ──────────────────────────────────────────────────────────────────────────────
def configure_ospf(net):
    """
    Cấu hình OSPF area 0 trên tất cả P và PE.
    Quảng bá: loopback (/32), tất cả mạng transit backbone.
    Dùng 'network ... area 0' để đơn giản, không cần cấu hình từng interface.
    """
    info('*** Configuring OSPF on backbone...\n')

    ospf_cfg = {
        'P1': {
            'rid': ROUTER_ID['P1'],
            'nets': [
                '10.0.0.1/32',     # loopback
                '10.1.1.0/30',     # P1-P2
                '10.1.1.4/30',     # P1-P3
                '10.1.1.16/30',    # P1-P4
                '10.1.2.0/30',     # P1-PE1
            ]
        },
        'P2': {
            'rid': ROUTER_ID['P2'],
            'nets': [
                '10.0.0.2/32',
                '10.1.1.0/30',     # P1-P2
                '10.1.1.8/30',     # P2-P4
                '10.1.1.20/30',    # P2-P3
                '10.1.2.12/30',    # P2-PE3
            ]
        },
        'P3': {
            'rid': ROUTER_ID['P3'],
            'nets': [
                '10.0.0.3/32',
                '10.1.1.4/30',     # P1-P3
                '10.1.1.12/30',    # P3-P4
                '10.1.1.20/30',    # P2-P3
                '10.1.2.4/30',     # P3-PE2
            ]
        },
        'P4': {
            'rid': ROUTER_ID['P4'],
            'nets': [
                '10.0.0.4/32',
                '10.1.1.8/30',     # P2-P4
                '10.1.1.12/30',    # P3-P4
                '10.1.1.16/30',    # P1-P4
                '10.1.2.8/30',     # P4-PE2
                '10.1.2.16/30',    # P4-PE3
            ]
        },
        'PE1': {
            'rid': ROUTER_ID['PE1'],
            'nets': [
                '10.0.0.11/32',
                '10.1.2.0/30',     # P1-PE1
            ]
        },
        'PE2': {
            'rid': ROUTER_ID['PE2'],
            'nets': [
                '10.0.0.12/32',
                '10.1.2.4/30',     # P3-PE2
                '10.1.2.8/30',     # P4-PE2
            ]
        },
        'PE3': {
            'rid': ROUTER_ID['PE3'],
            'nets': [
                '10.0.0.13/32',
                '10.1.2.12/30',    # P2-PE3
                '10.1.2.16/30',    # P4-PE3
            ]
        },
    }

    for node_name, cfg in ospf_cfg.items():
        node = net[node_name]
        rid  = cfg['rid']
        # Build vtysh command block
        net_cmds = '\n'.join(f' network {n} area 0' for n in cfg['nets'])
        cmds = (
            f'router ospf\n'
            f' ospf router-id {rid}\n'
            f' passive-interface default\n'   # mặc định passive, mở từng cổng
            f'{net_cmds}\n'
            f' no passive-interface lo\n'
        )
        # Mở các interface kết nối backbone (không passive)
        _open_ospf_intfs(node, node_name, cmds)


def _open_ospf_intfs(node, name: str, base_cmds: str):
    """Bổ sung 'no passive-interface' cho các interface P/PE."""
    intf_map = {
        'P1':  ['P1-eth0','P1-eth1','P1-eth2','P1-eth3'],
        'P2':  ['P2-eth0','P2-eth1','P2-eth2','P2-eth3'],
        'P3':  ['P3-eth0','P3-eth1','P3-eth2','P3-eth3'],
        'P4':  ['P4-eth0','P4-eth1','P4-eth2','P4-eth3','P4-eth4'],
        'PE1': ['PE1-eth0'],
        'PE2': ['PE2-eth0','PE2-eth1'],
        'PE3': ['PE3-eth0','PE3-eth1'],
    }
    no_passive = '\n'.join(
        f' no passive-interface {i}' for i in intf_map.get(name, [])
    )
    full_cmd = base_cmds + '\n' + no_passive
    vtysh_cmd(node, full_cmd)


# ──────────────────────────────────────────────────────────────────────────────
# 6. CONFIGURE LDP
# ──────────────────────────────────────────────────────────────────────────────
def configure_ldp(net):
    """
    Cấu hình LDP trên các liên kết P-P và P-PE.
    Router-ID của LDP phải là loopback (đã quảng bá qua OSPF).
    """
    info('*** Configuring LDP on backbone...\n')

    ldp_cfg = {
        'P1': {
            'rid':      ROUTER_ID['P1'],
            'trans_ip': ROUTER_ID['P1'],
            'intfs':    ['P1-eth0','P1-eth1','P1-eth2','P1-eth3'],
        },
        'P2': {
            'rid':      ROUTER_ID['P2'],
            'trans_ip': ROUTER_ID['P2'],
            'intfs':    ['P2-eth0','P2-eth1','P2-eth2','P2-eth3'],
        },
        'P3': {
            'rid':      ROUTER_ID['P3'],
            'trans_ip': ROUTER_ID['P3'],
            'intfs':    ['P3-eth0','P3-eth1','P3-eth2','P3-eth3'],
        },
        'P4': {
            'rid':      ROUTER_ID['P4'],
            'trans_ip': ROUTER_ID['P4'],
            'intfs':    ['P4-eth0','P4-eth1','P4-eth2','P4-eth3','P4-eth4'],
        },
        'PE1': {
            'rid':      ROUTER_ID['PE1'],
            'trans_ip': ROUTER_ID['PE1'],
            'intfs':    ['PE1-eth0'],
        },
        'PE2': {
            'rid':      ROUTER_ID['PE2'],
            'trans_ip': ROUTER_ID['PE2'],
            'intfs':    ['PE2-eth0','PE2-eth1'],
        },
        'PE3': {
            'rid':      ROUTER_ID['PE3'],
            'trans_ip': ROUTER_ID['PE3'],
            'intfs':    ['PE3-eth0','PE3-eth1'],
        },
    }

    for node_name, cfg in ldp_cfg.items():
        node = net[node_name]
        intf_cmds = '\n'.join(
            f' interface {i}\n  discovery transport-address {cfg["trans_ip"]}'
            for i in cfg['intfs']
        )
        cmds = (
            f'mpls ldp\n'
            f' router-id {cfg["rid"]}\n'
            f' address-family ipv4\n'
            f'  discovery transport-address {cfg["trans_ip"]}\n'
            + '\n'.join(f'  interface {i}' for i in cfg['intfs']) + '\n'
            f' exit-address-family\n'
        )
        vtysh_cmd(node, cmds)


# ──────────────────────────────────────────────────────────────────────────────
# 7. CONFIGURE VPLS / GRE PSEUDOWIRE (Metro Ethernet Fallback)
# ──────────────────────────────────────────────────────────────────────────────
def configure_vpls_gre(net):
    """
    FALLBACK NOTE:
    FRRouting trong Mininet (phiên bản thông thường qua apt) KHÔNG hỗ trợ
    đầy đủ VPLS/L2VPN lệnh YANG (l2vpn evpn, pseudowire-class, xconnect).
    Tính năng VPLS trong FRR yêu cầu build từ source với LDP VPLS patch.

    GIẢI PHÁP FALLBACK (vẫn giữ tinh thần Metro Ethernet):
    - Tạo GRE tunnel point-to-point giữa loopback PE1 và PE3.
    - GRE endpoint dùng loopback IP (reachable qua MPLS/OSPF underlay).
    - Bridge CE-facing interface với GRE tunnel trên mỗi PE.
    - Kết quả: Frame L2 từ host CN1 truyền xuyên qua MPLS backbone tới CN3.
    - Gói tin thực sự đi qua MPLS LSP vì loopback PE được học qua LDP.

    Đây là kỹ thuật GRE-over-MPLS được dùng rộng rãi trong thực tế
    khi thiết bị không hỗ trợ full VPLS signaling.
    """
    info('*** Configuring GRE pseudowire (Metro Ethernet L2 fallback)...\n')
    info('    [NOTE] Full FRR VPLS not available in standard apt package.\n')
    info('    [NOTE] Using GRE tunnel over MPLS loopback as pseudowire.\n')

    PE1 = net['PE1']
    PE3 = net['PE3']

    pe1_lo = strip_prefix(ROUTER_ID['PE1'])   # 10.0.0.11
    pe3_lo = strip_prefix(ROUTER_ID['PE3'])   # 10.0.0.13

    # Tạo GRE tunnel PE1 -> PE3
    create_gre_tunnel(PE1, 'gre-pw0', pe1_lo, pe3_lo, key=VPLS_KEY_CN1_CN3)
    # Tạo GRE tunnel PE3 -> PE1
    create_gre_tunnel(PE3, 'gre-pw0', pe3_lo, pe1_lo, key=VPLS_KEY_CN1_CN3)

    # Bridge CE interface + GRE tunnel trên PE1
    # PE1-eth1 nối CE1 (sẽ được add từ topology.py)
    create_linux_bridge(PE1, 'br-vpls', ['PE1-eth1', 'gre-pw0'])

    # Bridge CE interface + GRE tunnel trên PE3
    # PE3-eth2 nối CE3
    create_linux_bridge(PE3, 'br-vpls', ['PE3-eth2', 'gre-pw0'])

    info('    GRE pseudowire PE1 <-> PE3 configured (Metro Ethernet L2 service)\n')


# ──────────────────────────────────────────────────────────────────────────────
# 8. WAIT FOR CONVERGENCE
# ──────────────────────────────────────────────────────────────────────────────
def wait_convergence():
    """Đợi OSPF và LDP hội tụ trước khi đo test."""
    info(f'*** Waiting {OSPF_WAIT_SEC}s for OSPF convergence...\n')
    time.sleep(OSPF_WAIT_SEC)
    info(f'*** Waiting {LDP_WAIT_SEC}s for LDP to establish LSPs...\n')
    time.sleep(LDP_WAIT_SEC)


# ──────────────────────────────────────────────────────────────────────────────
# 9. VERIFICATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def verify_backbone(net):
    """In ra trạng thái OSPF, LDP, MPLS table để kiểm tra."""
    info('\n*** === BACKBONE VERIFICATION ===\n')
    for name in ['PE1', 'PE2', 'PE3', 'P1']:
        node = net[name]
        info(f'\n--- {name} OSPF neighbors ---\n')
        out = vtysh_exec(node, 'show ip ospf neighbor')
        info(out + '\n')
        info(f'\n--- {name} MPLS LDP neighbor ---\n')
        out = vtysh_exec(node, 'show mpls ldp neighbor')
        info(out + '\n')
        info(f'\n--- {name} MPLS table ---\n')
        out = node.cmd('ip -M route 2>/dev/null || ip route show table all | grep mpls 2>/dev/null || echo "no mpls table cmd"')
        info(out + '\n')
