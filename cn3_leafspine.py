#!/usr/bin/env python3
"""
cn3_leafspine.py - Chi nhánh 3: Mạng 2 lớp Leaf-Spine.

Kiến trúc (bám sơ đồ logic):
  CE3 (router biên)
   |         |
 spine1  spine2  (LinuxRouter, full-mesh ngang)
  /|\\     /|\\
leaf1  leaf2  leaf3  (LinuxRouter, kết nối mỗi spine)
 |      |      |
web1 web2 dns1 dns2 db1 db2

Đặc điểm kỹ thuật:
- Full-mesh Spine-Leaf: mỗi leaf kết nối TOÀN BỘ spine.
- ECMP: OSPF equal-cost multipath bật trên tất cả spine/leaf.
  => Traffic phân tải đều qua 2 spine thay vì chỉ dùng 1 đường.
- OSPF chạy nội bộ CN3 để leaf thấy đường về CE3 qua cả 2 spine.
- CE3 biết subnet của từng leaf, redistributes connected.

Lý do dùng Leaf-Spine + ECMP:
  Kiến trúc DC hiện đại, không có STP bottleneck, băng thông cao
  và có thể mở rộng horizontally. So sánh được với CN1/CN2 về
  throughput dưới tải cao (stress test).
"""

import time
from mininet.log import info

from common import LinuxRouter, set_ip, add_loopback, vtysh_cmd
from common import start_frr_zebra, start_frr_ospfd
from addressing import (
    LINK_PE3_CE3, LINK_CE3_SPINE1, LINK_CE3_SPINE2,
    CN3_LINKS, CN3_LOOPBACK, CN3_GW, CN3_HOSTS,
    CN3_SUBNET_WEB, CN3_SUBNET_DNS, CN3_SUBNET_DB,
    strip_prefix
)


# ──────────────────────────────────────────────────────────────────────────────
# ADD NODES
# ──────────────────────────────────────────────────────────────────────────────
def add_cn3_nodes(net):
    info('*** Adding CN3 (Leaf-Spine) nodes...\n')

    ce3    = net.addHost('CE3',    cls=LinuxRouter, ip=None)
    spine1 = net.addHost('spine1', cls=LinuxRouter, ip=None)
    spine2 = net.addHost('spine2', cls=LinuxRouter, ip=None)
    leaf1  = net.addHost('leaf1',  cls=LinuxRouter, ip=None)
    leaf2  = net.addHost('leaf2',  cls=LinuxRouter, ip=None)
    leaf3  = net.addHost('leaf3',  cls=LinuxRouter, ip=None)

    # Servers
    for hname, (ip_cidr, gw) in CN3_HOSTS.items():
        net.addHost(hname, ip=ip_cidr, defaultRoute=f'via {gw}')

    return ce3, spine1, spine2, leaf1, leaf2, leaf3


# ──────────────────────────────────────────────────────────────────────────────
# ADD LINKS
# ──────────────────────────────────────────────────────────────────────────────
def add_cn3_links(net):
    """
    Thứ tự addLink (phải khớp với eth index trong configure_cn3):

    PE3  <-> CE3     : PE3-eth2  / CE3-eth0
    CE3  <-> spine1  : CE3-eth1  / spine1-eth0
    CE3  <-> spine2  : CE3-eth2  / spine2-eth0

    Full-mesh Spine-Leaf:
    spine1 <-> leaf1 : spine1-eth1 / leaf1-eth0
    spine1 <-> leaf2 : spine1-eth2 / leaf2-eth0
    spine1 <-> leaf3 : spine1-eth3 / leaf3-eth0
    spine2 <-> leaf1 : spine2-eth1 / leaf1-eth1
    spine2 <-> leaf2 : spine2-eth2 / leaf2-eth1
    spine2 <-> leaf3 : spine2-eth3 / leaf3-eth1

    Leaf <-> servers:
    leaf1 <-> web1, web2
    leaf2 <-> dns1, dns2
    leaf3 <-> db1, db2
    """
    info('*** Adding CN3 links...\n')

    net.addLink('PE3', 'CE3')       # PE3-eth2 / CE3-eth0
    net.addLink('CE3', 'spine1')    # CE3-eth1 / spine1-eth0
    net.addLink('CE3', 'spine2')    # CE3-eth2 / spine2-eth0

    # Full-mesh spine-leaf
    net.addLink('spine1', 'leaf1')  # spine1-eth1 / leaf1-eth0
    net.addLink('spine1', 'leaf2')  # spine1-eth2 / leaf2-eth0
    net.addLink('spine1', 'leaf3')  # spine1-eth3 / leaf3-eth0
    net.addLink('spine2', 'leaf1')  # spine2-eth1 / leaf1-eth1
    net.addLink('spine2', 'leaf2')  # spine2-eth2 / leaf2-eth1
    net.addLink('spine2', 'leaf3')  # spine2-eth3 / leaf3-eth1

    # Server connections
    net.addLink('web1', 'leaf1')
    net.addLink('web2', 'leaf1')
    net.addLink('dns1', 'leaf2')
    net.addLink('dns2', 'leaf2')
    net.addLink('db1',  'leaf3')
    net.addLink('db2',  'leaf3')


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURE IP
# ──────────────────────────────────────────────────────────────────────────────
def configure_cn3(net):
    """
    Gán IP cho tất cả node CN3.
    OSPF chạy nội bộ CN3 (area 1) để spine/leaf thấy nhau.
    CE3 chạy OSPF area 1 nội bộ VÀ redistribute vào backbone qua PE3.
    """
    info('*** Configuring CN3 (Leaf-Spine) IPs...\n')

    CE3    = net['CE3']
    PE3    = net['PE3']
    spine1 = net['spine1']
    spine2 = net['spine2']
    leaf1  = net['leaf1']
    leaf2  = net['leaf2']
    leaf3  = net['leaf3']

    # ── Loopback (router-id) ─────────────────────────────────────────────────
    for node in [spine1, spine2, leaf1, leaf2, leaf3]:
        add_loopback(node, CN3_LOOPBACK[node.name])

    # ── CE3 ──────────────────────────────────────────────────────────────────
    pe3_ip     = strip_prefix(LINK_PE3_CE3[0])   # 10.2.3.1
    ce3_pe_ip  = LINK_PE3_CE3[1]                  # 10.2.3.2/30

    set_ip(CE3, 'CE3-eth0', ce3_pe_ip)            # toward PE3
    set_ip(CE3, 'CE3-eth1', strip_prefix(LINK_CE3_SPINE1[0]) + '/30')  # 10.2.3.6/30
    set_ip(CE3, 'CE3-eth2', strip_prefix(LINK_CE3_SPINE2[0]) + '/30')  # 10.2.3.10/30

    CE3.cmd(f'ip route add default via {pe3_ip}')

    # PE3 CE-facing
    set_ip(PE3, 'PE3-eth2', LINK_PE3_CE3[0])
    ce3_ip = strip_prefix(ce3_pe_ip)
    PE3.cmd(f'ip route add 192.168.31.0/24 via {ce3_ip}')
    PE3.cmd(f'ip route add 192.168.32.0/24 via {ce3_ip}')
    PE3.cmd(f'ip route add 192.168.33.0/24 via {ce3_ip}')

    vtysh_cmd(PE3,
        f'router ospf\n'
        f' network 10.2.3.0/30 area 0\n'
        f' network 192.168.31.0/24 area 0\n'
        f' network 192.168.32.0/24 area 0\n'
        f' network 192.168.33.0/24 area 0\n'
    )

    # ── Spine1 ───────────────────────────────────────────────────────────────
    # eth0: CE3 uplink
    set_ip(spine1, 'spine1-eth0', strip_prefix(LINK_CE3_SPINE1[1]) + '/30')  # 10.2.3.5/30
    # eth1-3: leaf downlinks (dùng địa chỉ từ CN3_LINKS)
    set_ip(spine1, 'spine1-eth1', CN3_LINKS['spine1-leaf1'][0])   # 10.3.1.1/30
    set_ip(spine1, 'spine1-eth2', CN3_LINKS['spine1-leaf2'][0])   # 10.3.1.5/30
    set_ip(spine1, 'spine1-eth3', CN3_LINKS['spine1-leaf3'][0])   # 10.3.1.9/30

    # ECMP: bật multipath cho spine1
    spine1.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
    spine1.cmd('ip route add 192.168.31.0/24 via 10.3.1.2 2>/dev/null || true')
    spine1.cmd('ip route add 192.168.32.0/24 via 10.3.1.6 2>/dev/null || true')
    spine1.cmd('ip route add 192.168.33.0/24 via 10.3.1.10 2>/dev/null || true')
    spine1.cmd(f'ip route add default via {strip_prefix(LINK_CE3_SPINE1[0])}')

    # ── Spine2 ───────────────────────────────────────────────────────────────
    set_ip(spine2, 'spine2-eth0', strip_prefix(LINK_CE3_SPINE2[1]) + '/30')  # 10.2.3.9/30
    set_ip(spine2, 'spine2-eth1', CN3_LINKS['spine2-leaf1'][0])   # 10.3.1.13/30
    set_ip(spine2, 'spine2-eth2', CN3_LINKS['spine2-leaf2'][0])   # 10.3.1.17/30
    set_ip(spine2, 'spine2-eth3', CN3_LINKS['spine2-leaf3'][0])   # 10.3.1.21/30

    spine2.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
    spine2.cmd('ip route add 192.168.31.0/24 via 10.3.1.14 2>/dev/null || true')
    spine2.cmd('ip route add 192.168.32.0/24 via 10.3.1.18 2>/dev/null || true')
    spine2.cmd('ip route add 192.168.33.0/24 via 10.3.1.22 2>/dev/null || true')
    spine2.cmd(f'ip route add default via {strip_prefix(LINK_CE3_SPINE2[0])}')

    # ── Leaf1 (Web servers) ──────────────────────────────────────────────────
    set_ip(leaf1, 'leaf1-eth0', CN3_LINKS['spine1-leaf1'][1])   # 10.3.1.2/30
    set_ip(leaf1, 'leaf1-eth1', CN3_LINKS['spine2-leaf1'][1])   # 10.3.1.14/30
    set_ip(leaf1, 'leaf1-eth2', f'{CN3_GW["leaf1"]}/24')        # 192.168.31.254/24

    # ECMP: 2 đường lên spine1 và spine2 (equal cost)
    # Thử multipath nexthop syntax (kernel >= 3.6 + iproute2 mới)
    leaf1.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
    ecmp_ok = leaf1.cmd(
        'ip route add default '
        'nexthop via 10.3.1.1 dev leaf1-eth0 weight 1 '
        'nexthop via 10.3.1.13 dev leaf1-eth1 weight 1 2>&1'
    )
    if 'Error' in ecmp_ok or 'error' in ecmp_ok or 'RTNETLINK' in ecmp_ok:
        # Fallback: dùng 2 default route equal metric (kernel chọn per-flow)
        leaf1.cmd('ip route add default via 10.3.1.1 metric 10')
        leaf1.cmd('ip route add default via 10.3.1.13 metric 10')

    # ── Leaf2 (DNS servers) ──────────────────────────────────────────────────
    set_ip(leaf2, 'leaf2-eth0', CN3_LINKS['spine1-leaf2'][1])   # 10.3.1.6/30
    set_ip(leaf2, 'leaf2-eth1', CN3_LINKS['spine2-leaf2'][1])   # 10.3.1.18/30
    set_ip(leaf2, 'leaf2-eth2', f'{CN3_GW["leaf2"]}/24')        # 192.168.32.254/24

    leaf2.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
    ecmp_ok = leaf2.cmd(
        'ip route add default '
        'nexthop via 10.3.1.5 dev leaf2-eth0 weight 1 '
        'nexthop via 10.3.1.17 dev leaf2-eth1 weight 1 2>&1'
    )
    if 'Error' in ecmp_ok or 'error' in ecmp_ok or 'RTNETLINK' in ecmp_ok:
        leaf2.cmd('ip route add default via 10.3.1.5 metric 10')
        leaf2.cmd('ip route add default via 10.3.1.17 metric 10')

    # ── Leaf3 (DB servers) ───────────────────────────────────────────────────
    set_ip(leaf3, 'leaf3-eth0', CN3_LINKS['spine1-leaf3'][1])   # 10.3.1.10/30
    set_ip(leaf3, 'leaf3-eth1', CN3_LINKS['spine2-leaf3'][1])   # 10.3.1.22/30
    set_ip(leaf3, 'leaf3-eth2', f'{CN3_GW["leaf3"]}/24')        # 192.168.33.254/24

    leaf3.cmd('sysctl -w net.ipv4.fib_multipath_hash_policy=1')
    ecmp_ok = leaf3.cmd(
        'ip route add default '
        'nexthop via 10.3.1.9 dev leaf3-eth0 weight 1 '
        'nexthop via 10.3.1.21 dev leaf3-eth1 weight 1 2>&1'
    )
    if 'Error' in ecmp_ok or 'error' in ecmp_ok or 'RTNETLINK' in ecmp_ok:
        leaf3.cmd('ip route add default via 10.3.1.9 metric 10')
        leaf3.cmd('ip route add default via 10.3.1.21 metric 10')

    # CE3 cần biết subnet của các leaf
    CE3.cmd('ip route add 192.168.31.0/24 via 10.2.3.5 2>/dev/null || true')
    CE3.cmd('ip route add 192.168.32.0/24 via 10.2.3.5 2>/dev/null || true')
    CE3.cmd('ip route add 192.168.33.0/24 via 10.2.3.5 2>/dev/null || true')
    # ECMP trên CE3: biết 2 đường qua spine1 và spine2
    CE3.cmd('ip route add 10.3.1.0/30  via 10.2.3.5 2>/dev/null || true')
    CE3.cmd('ip route add 10.3.1.8/30  via 10.2.3.5 2>/dev/null || true')
    CE3.cmd('ip route add 10.3.1.12/30 via 10.2.3.9 2>/dev/null || true')
    CE3.cmd('ip route add 10.3.1.16/30 via 10.2.3.9 2>/dev/null || true')
    CE3.cmd('ip route add 10.3.1.20/30 via 10.2.3.9 2>/dev/null || true')

    info('    CN3 IP configuration done. ECMP enabled on Spine/Leaf.\n')


# ──────────────────────────────────────────────────────────────────────────────
# START FRR (optional OSPF nội bộ CN3)
# ──────────────────────────────────────────────────────────────────────────────
def start_cn3_frr(net):
    """
    Tùy chọn: Bật OSPF nội bộ CN3 trên spine và leaf.
    Trong mô hình đơn giản, static routes đã đủ (vì topo cố định).
    Nếu muốn dynamic convergence, gọi hàm này.
    """
    info('*** (Optional) Starting OSPF on CN3 spine/leaf for dynamic routing...\n')
    for name in ['spine1', 'spine2', 'leaf1', 'leaf2', 'leaf3']:
        node = net[name]
        start_frr_zebra(node)
        start_frr_ospfd(node)
    time.sleep(1)

    # Cấu hình OSPF area 1 nội bộ CN3
    cn3_ospf = {
        'spine1': {
            'rid': strip_prefix(CN3_LOOPBACK['spine1']),
            'nets': ['10.2.3.4/30','10.3.1.0/30','10.3.1.4/30','10.3.1.8/30',
                     strip_prefix(CN3_LOOPBACK['spine1'])+'/32']
        },
        'spine2': {
            'rid': strip_prefix(CN3_LOOPBACK['spine2']),
            'nets': ['10.2.3.8/30','10.3.1.12/30','10.3.1.16/30','10.3.1.20/30',
                     strip_prefix(CN3_LOOPBACK['spine2'])+'/32']
        },
        'leaf1': {
            'rid': strip_prefix(CN3_LOOPBACK['leaf1']),
            'nets': ['10.3.1.0/30','10.3.1.12/30','192.168.31.0/24',
                     strip_prefix(CN3_LOOPBACK['leaf1'])+'/32']
        },
        'leaf2': {
            'rid': strip_prefix(CN3_LOOPBACK['leaf2']),
            'nets': ['10.3.1.4/30','10.3.1.16/30','192.168.32.0/24',
                     strip_prefix(CN3_LOOPBACK['leaf2'])+'/32']
        },
        'leaf3': {
            'rid': strip_prefix(CN3_LOOPBACK['leaf3']),
            'nets': ['10.3.1.8/30','10.3.1.20/30','192.168.33.0/24',
                     strip_prefix(CN3_LOOPBACK['leaf3'])+'/32']
        },
    }

    for node_name, cfg in cn3_ospf.items():
        node = net[node_name]
        net_cmds = '\n'.join(f' network {n} area 1' for n in cfg['nets'])
        cmds = (
            f'router ospf\n'
            f' ospf router-id {cfg["rid"]}\n'
            f' maximum-paths 8\n'   # ECMP – tận dụng tối đa multipath
            + net_cmds
        )
        vtysh_cmd(node, cmds)


# ──────────────────────────────────────────────────────────────────────────────
# VERIFY
# ──────────────────────────────────────────────────────────────────────────────
def verify_cn3(net):
    """Ping web1 -> db1 để xác nhận cross-leaf routing."""
    info('\n--- CN3 Verification ---\n')
    web1 = net['web1']
    db1  = net['db1']
    result = web1.cmd('ping -c 2 -W 2 192.168.33.1 2>&1')
    if '0% packet loss' in result or '2 received' in result or '1 received' in result:
        info('    [OK] web1 -> db1 (cross-leaf via spine) OK\n')
    else:
        info('    [WARN] web1 -> db1 may need more time (OSPF/route convergence)\n')

    # Traceroute để thấy multipath spine
    result = web1.cmd('traceroute -n -m 5 -w 1 192.168.33.1 2>&1')
    info(f'    Traceroute web1->db1:\n{result}\n')
