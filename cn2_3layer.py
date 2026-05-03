#!/usr/bin/env python3
"""
cn2_3layer.py - Chi nhánh 2: Mạng 3 lớp (Core - Distribution - Access).

Kiến trúc (bám sơ đồ logic):
  CE2 (router biên)
    |
  core_sw (OVS switch, lớp Core)
   /   \
  dist1  dist2 (LinuxRouter, lớp Distribution – thực hiện inter-VLAN routing)
   |       |
  acc1   acc2  acc3 (OVS switch, lớp Access)
  |       |       |
 admin  lab    guest  (hosts)

VLAN phân chia:
  VLAN 10 – Admin: 192.168.21.0/24  acc1 -> dist1
  VLAN 20 – Lab:   192.168.22.0/24  acc2 -> dist1/dist2 (dual-homed)
  VLAN 30 – Guest: 192.168.23.0/24  acc3 -> dist2

Inter-VLAN routing: dist1 và dist2 là LinuxRouter với ip_forward=1.
  dist1: eth1=SVI VLAN10, eth2=SVI VLAN20
  dist2: eth1=SVI VLAN30, eth2=SVI VLAN20

Lý do dùng mô hình 3 lớp + VLAN:
  Kiến trúc chuẩn doanh nghiệp, phân chia traffic theo nhóm người dùng,
  cho phép QoS và ACL tại lớp Distribution.
  Phù hợp so sánh với flat (CN1) và leaf-spine (CN3) về hiệu năng.

NOTE: Mininet/OVS không hỗ trợ VLAN tagging đầy đủ như switch thật.
  Ta giả lập bằng cách:
  - Dùng LinuxRouter làm Distribution (thay vì OVS SVI) để có ip_forward.
  - Mỗi dist router có nhiều interface, mỗi interface tương ứng 1 subnet.
  - Access switch dùng OVS standalone (L2 only).
"""

from mininet.node import OVSSwitch
from mininet.log import info

from common import LinuxRouter, set_ip, vtysh_cmd
from addressing import (
    LINK_PE2_CE2, LINK_CE2_CORE,
    CN2_GW_ADMIN, CN2_GW_LAB, CN2_GW_GUEST,
    CN2_HOSTS, CN2_SUBNET_ADMIN, CN2_SUBNET_LAB, CN2_SUBNET_GUEST,
    strip_prefix
)


# ──────────────────────────────────────────────────────────────────────────────
# ADD NODES
# ──────────────────────────────────────────────────────────────────────────────
def add_cn2_nodes(net):
    info('*** Adding CN2 (3-Layer) nodes...\n')

    # CE2: router biên chi nhánh 2
    ce2 = net.addHost('CE2', cls=LinuxRouter, ip=None)

    # Core layer: 1 OVS switch (L2, tốc độ cao, không routing)
    core_sw = net.addSwitch('core_sw', cls=OVSSwitch, failMode='standalone')

    # Distribution layer: 2 LinuxRouter (inter-VLAN routing)
    dist1 = net.addHost('dist1', cls=LinuxRouter, ip=None)
    dist2 = net.addHost('dist2', cls=LinuxRouter, ip=None)

    # Access layer: 3 OVS switch
    acc1 = net.addSwitch('acc1', cls=OVSSwitch, failMode='standalone')
    acc2 = net.addSwitch('acc2', cls=OVSSwitch, failMode='standalone')
    acc3 = net.addSwitch('acc3', cls=OVSSwitch, failMode='standalone')

    # Hosts
    for hname, (ip_cidr, gw) in CN2_HOSTS.items():
        net.addHost(hname, ip=ip_cidr, defaultRoute=f'via {gw}')

    return ce2, core_sw, dist1, dist2, acc1, acc2, acc3


# ──────────────────────────────────────────────────────────────────────────────
# ADD LINKS
# ──────────────────────────────────────────────────────────────────────────────
def add_cn2_links(net):
    """
    Thứ tự addLink quyết định chỉ số eth.

    PE2  <-> CE2   : PE2-eth2 / CE2-eth0
    CE2  <-> core_sw: CE2-eth1 / core_sw-ethX
    core_sw <-> dist1: core_sw / dist1-eth0
    core_sw <-> dist2: core_sw / dist2-eth0
    dist1 <-> acc1  : dist1-eth1 / acc1
    dist1 <-> acc2  : dist1-eth2 / acc2   (dual-home lab)
    dist2 <-> acc2  : dist2-eth1 / acc2   (dual-home lab)
    dist2 <-> acc3  : dist2-eth2 / acc3
    acc1 <-> admin1,admin2
    acc2 <-> lab1, lab2
    acc3 <-> guest1, guest2
    """
    info('*** Adding CN2 links...\n')

    net.addLink('PE2', 'CE2')         # PE2-eth2 / CE2-eth0
    net.addLink('CE2', 'core_sw')     # CE2-eth1 / core_sw-ethX
    net.addLink('core_sw', 'dist1')   # dist1-eth0
    net.addLink('core_sw', 'dist2')   # dist2-eth0

    net.addLink('dist1', 'acc1')      # dist1-eth1 / acc1
    net.addLink('dist1', 'acc2')      # dist1-eth2 / acc2 (dual)
    net.addLink('dist2', 'acc2')      # dist2-eth1 / acc2 (dual)
    net.addLink('dist2', 'acc3')      # dist2-eth2 / acc3

    net.addLink('admin1', 'acc1')
    net.addLink('admin2', 'acc1')
    net.addLink('lab1', 'acc2')
    net.addLink('lab2', 'acc2')
    net.addLink('guest1', 'acc3')
    net.addLink('guest2', 'acc3')


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURE IP + INTER-VLAN ROUTING
# ──────────────────────────────────────────────────────────────────────────────
def configure_cn2(net):
    """
    Cấu hình IP và định tuyến 3 lớp.

    Kiến trúc routing CN2:
      PE2 (10.2.2.1/30) <-> CE2 (10.2.2.2/30)  [WAN uplink]
      CE2 (10.2.2.4/29) ──[L2 core_sw]── dist1 (10.2.2.5/29), dist2 (10.2.2.6/29)

    core_sw là OVS switch L2 thuần – trong suốt như wire.
    CE2 và dist1/dist2 cùng nằm trong subnet 10.2.2.4/29 qua L2 core_sw.
    => CE2, dist1, dist2 thấy nhau tại L3 mà không cần router trung gian.
    CE2 là L3 gateway duy nhất ra backbone.
    dist1/dist2 làm inter-VLAN router, default route về CE2.
    """
    info('*** Configuring CN2 (3-Layer) IPs and routing...\n')

    CE2   = net['CE2']
    PE2   = net['PE2']
    dist1 = net['dist1']
    dist2 = net['dist2']

    pe2_ip = strip_prefix(LINK_PE2_CE2[0])   # 10.2.2.1
    ce2_pe = LINK_PE2_CE2[1]                  # 10.2.2.2/30

    # ── CE2 ──────────────────────────────────────────────────────────────────
    set_ip(CE2, 'CE2-eth0', ce2_pe)            # toward PE2 (WAN)
    # CE2-eth1 -> core_sw (L2 transparent) -> dist1 và dist2
    # Dùng /29 (8 hosts) để CE2(.4), dist1(.5), dist2(.6) trong cùng subnet
    set_ip(CE2, 'CE2-eth1', '10.2.2.4/29')

    CE2.cmd(f'ip route add default via {pe2_ip}')
    # CE2 biết VLAN subnets via dist (qua L2 transparent core_sw)
    CE2.cmd('ip route add 192.168.21.0/24 via 10.2.2.5')
    CE2.cmd('ip route add 192.168.22.0/24 via 10.2.2.5')
    CE2.cmd('ip route add 192.168.23.0/24 via 10.2.2.6')

    # ── PE2 phía CE-facing ────────────────────────────────────────────────────
    set_ip(PE2, 'PE2-eth2', LINK_PE2_CE2[0])
    ce2_ip = strip_prefix(ce2_pe)
    PE2.cmd(f'ip route add 192.168.21.0/24 via {ce2_ip}')
    PE2.cmd(f'ip route add 192.168.22.0/24 via {ce2_ip}')
    PE2.cmd(f'ip route add 192.168.23.0/24 via {ce2_ip}')

    # Quảng bá subnet CN2 vào OSPF backbone
    from common import vtysh_cmd
    vtysh_cmd(PE2,
        'router ospf\n'
        ' network 10.2.2.0/30 area 0\n'
        ' network 192.168.21.0/24 area 0\n'
        ' network 192.168.22.0/24 area 0\n'
        ' network 192.168.23.0/24 area 0\n'
    )

    # ── dist1 (VLAN10 Admin + VLAN20 Lab) ────────────────────────────────────
    # dist1-eth0: uplink qua core_sw -> L2 segment 10.2.2.4/29
    set_ip(dist1, 'dist1-eth0', '10.2.2.5/29')
    # dist1-eth1: gateway VLAN10 (Admin hosts trên acc1)
    set_ip(dist1, 'dist1-eth1', f'{CN2_GW_ADMIN}/24')    # 192.168.21.254/24
    # dist1-eth2: gateway VLAN20 (Lab hosts trên acc2, primary)
    set_ip(dist1, 'dist1-eth2', f'{CN2_GW_LAB}/24')      # 192.168.22.254/24
    dist1.cmd('ip route add default via 10.2.2.4')        # default -> CE2

    # ── dist2 (VLAN20 redundant + VLAN30 Guest) ───────────────────────────────
    # dist2-eth0: uplink qua core_sw
    set_ip(dist2, 'dist2-eth0', '10.2.2.6/29')
    # dist2-eth1: VLAN20 redundant (.253 để không conflict với dist1's .254)
    set_ip(dist2, 'dist2-eth1', '192.168.22.253/24')
    # dist2-eth2: gateway VLAN30 (Guest hosts trên acc3)
    set_ip(dist2, 'dist2-eth2', f'{CN2_GW_GUEST}/24')    # 192.168.23.254/24
    dist2.cmd('ip route add default via 10.2.2.4')

    info(f'    CE2: WAN=10.2.2.2/30, core_L2=10.2.2.4/29\n')
    info(f'    dist1: uplink=10.2.2.5/29, Admin={CN2_GW_ADMIN}/24, Lab={CN2_GW_LAB}/24\n')
    info(f'    dist2: uplink=10.2.2.6/29, Guest={CN2_GW_GUEST}/24\n')


# ──────────────────────────────────────────────────────────────────────────────
# VERIFY
# ──────────────────────────────────────────────────────────────────────────────
def verify_cn2(net):
    """Ping inter-VLAN để xác nhận distribution routing hoạt động."""
    info('\n--- CN2 Verification ---\n')
    # Intra-VLAN (cùng subnet qua access switch)
    a1 = net['admin1']
    a2 = net['admin2']
    result = a1.cmd('ping -c 2 -W 1 192.168.21.2 2>&1')
    if '0% packet loss' in result or '2 received' in result:
        info('    [OK] admin1 -> admin2 (intra-VLAN10) OK\n')
    else:
        info('    [WARN] admin1 -> admin2 failed (check acc1 switch)\n')

    # Inter-VLAN: admin1 -> lab1
    result = a1.cmd('ping -c 2 -W 2 192.168.22.1 2>&1')
    if '0% packet loss' in result or '2 received' in result or '1 received' in result:
        info('    [OK] admin1 -> lab1 (inter-VLAN10->VLAN20) OK\n')
    else:
        info('    [WARN] admin1 -> lab1 inter-VLAN may need more time\n')
