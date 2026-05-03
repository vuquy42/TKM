#!/usr/bin/env python3
"""
cn1_flat.py - Chi nhánh 1: Mạng phẳng (Flat Network).

Kiến trúc:
  CE1 (router) <--> sw_cn1 (OVS switch) <--> host1..host4

  Tất cả host chung subnet 192.168.10.0/24, gateway là CE1.
  CE1 kết nối backbone qua PE1 (link PE1-eth1 <-> CE1-eth0).
  CE1 cũng có eth1 làm default gateway cho LAN.

Lý do dùng Flat:
  Mô hình đơn giản nhất, broadcast domain duy nhất.
  Dễ so sánh với 3-layer và leaf-spine về throughput/delay.
"""

from mininet.node import OVSSwitch
from mininet.log import info

from common import LinuxRouter, set_ip
from addressing import (
    LINK_PE1_CE1, CN1_GW, CN1_HOSTS, CN1_SUBNET, strip_prefix
)


# ──────────────────────────────────────────────────────────────────────────────
# ADD NODES
# ──────────────────────────────────────────────────────────────────────────────
def add_cn1_nodes(net):
    """Thêm CE1, sw_cn1, và host1-4 vào net."""
    info('*** Adding CN1 (Flat) nodes...\n')

    # CE1: router biên của chi nhánh 1
    ce1 = net.addHost('CE1', cls=LinuxRouter, ip=None)

    # sw_cn1: switch tầng LAN (không cần OVS controller)
    sw1 = net.addSwitch('sw_cn1', cls=OVSSwitch, failMode='standalone')

    # host1-4: đại diện endpoint chi nhánh 1
    for hname, ip_cidr in CN1_HOSTS.items():
        net.addHost(hname, ip=ip_cidr, defaultRoute=f'via {CN1_GW}')

    return ce1, sw1


# ──────────────────────────────────────────────────────────────────────────────
# ADD LINKS
# ──────────────────────────────────────────────────────────────────────────────
def add_cn1_links(net):
    """
    Nối dây CN1.
    PE1-eth1 <-> CE1-eth0  (WAN/backbone side)
    CE1-eth1  <-> sw_cn1-eth0
    sw_cn1    <-> host1..host4
    """
    info('*** Adding CN1 links...\n')

    # PE1 <-> CE1 (link backbone phía PE1 đã có eth0 về P1, eth1 sẽ về CE1)
    net.addLink('PE1', 'CE1')   # PE1-eth1 / CE1-eth0

    # CE1 <-> switch LAN
    net.addLink('CE1', 'sw_cn1')  # CE1-eth1 / sw_cn1-eth0

    # Host kết nối vào switch
    for hname in CN1_HOSTS:
        net.addLink(hname, 'sw_cn1')


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURE IP
# ──────────────────────────────────────────────────────────────────────────────
def configure_cn1(net):
    """
    Cấu hình IP cho CE1 và các host CN1.
    Định tuyến CE-PE: Static route.
    CE1 có default route về PE1 (backbone side).
    PE1 có static route về 192.168.10.0/24 via CE1.
    """
    info('*** Configuring CN1 (Flat Network) IPs...\n')

    CE1 = net['CE1']
    PE1 = net['PE1']

    # CE1 backbone-facing interface (eth0 = link về PE1)
    pe1_ce1_pe_ip = strip_prefix(LINK_PE1_CE1[0])   # 10.2.1.1 (PE1 side)
    ce1_backbone_ip = LINK_PE1_CE1[1]                # 10.2.1.2/30 (CE1 side)

    set_ip(CE1, 'CE1-eth0', ce1_backbone_ip)

    # CE1 LAN-facing interface (eth1 = link về sw_cn1)
    set_ip(CE1, 'CE1-eth1', f'{CN1_GW}/24')

    # Default route CE1 -> PE1 (ra Internet/backbone)
    CE1.cmd(f'ip route add default via {pe1_ce1_pe_ip}')

    # PE1 phía CE-facing interface
    set_ip(PE1, 'PE1-eth1', LINK_PE1_CE1[0])

    # Static route trên PE1: biết subnet CN1 qua CE1
    ce1_ip = strip_prefix(ce1_backbone_ip)
    PE1.cmd(f'ip route add {CN1_SUBNET} via {ce1_ip}')

    # Quảng bá subnet CN1 vào OSPF trên PE1 (để backbone biết)
    # Dùng 'redistribute connected' hoặc thêm network statement
    from common import vtysh_cmd
    vtysh_cmd(PE1, f'router ospf\n network {CN1_SUBNET} area 0\n network 10.2.1.0/30 area 0')

    info(f'    CE1: eth0={ce1_backbone_ip}, eth1={CN1_GW}/24\n')
    info(f'    Hosts: {CN1_SUBNET} gw={CN1_GW}\n')


# ──────────────────────────────────────────────────────────────────────────────
# VERIFY
# ──────────────────────────────────────────────────────────────────────────────
def verify_cn1(net):
    """Ping nhanh host1 -> host4 để xác nhận LAN thông."""
    info('\n--- CN1 Verification ---\n')
    h1 = net['host1']
    h4 = net['host4']
    result = h1.cmd(f'ping -c 2 -W 1 {strip_prefix(CN1_HOSTS["host4"])} 2>&1')
    if '0% packet loss' in result or '1 received' in result or '2 received' in result:
        info('    [OK] host1 -> host4 reachable within CN1\n')
    else:
        info('    [WARN] host1 -> host4 may not be reachable yet (ARP learning)\n')
