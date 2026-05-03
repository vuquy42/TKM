#!/usr/bin/env python3
"""
addressing.py - Quy hoạch địa chỉ IP toàn bộ dự án Metro Ethernet MPLS.

Quy ước đặt tên interface:
  <node>-eth<N>   : cổng vật lý Mininet tự tạo
  <node>-lo       : loopback thêm vào bằng lệnh ip addr add

Tất cả địa chỉ IP lưu ở đây để một chỗ duy nhất, tránh hardcode rải rác.
"""

# ══════════════════════════════════════════════════════════════════════════════
# LOOPBACK (Router-ID cho OSPF & LDP)
# ══════════════════════════════════════════════════════════════════════════════
LOOPBACK = {
    # Provider (P)
    'P1':  '10.0.0.1/32',
    'P2':  '10.0.0.2/32',
    'P3':  '10.0.0.3/32',
    'P4':  '10.0.0.4/32',
    # Provider Edge (PE)
    'PE1': '10.0.0.11/32',
    'PE2': '10.0.0.12/32',
    'PE3': '10.0.0.13/32',
}

# Địa chỉ loopback không có prefix-length (dùng để cấu hình OSPF/LDP)
ROUTER_ID = {k: v.split('/')[0] for k, v in LOOPBACK.items()}

# ══════════════════════════════════════════════════════════════════════════════
# BACKBONE LINKS  (P-P và P-PE)
# Dải 10.1.x.x/30
# ══════════════════════════════════════════════════════════════════════════════
# Liên kết P-P (full-mesh một phần tạo dự phòng)
LINK_P1_P2  = ('10.1.1.1/30',  '10.1.1.2/30')    # P1-eth0 / P2-eth0
LINK_P1_P3  = ('10.1.1.5/30',  '10.1.1.6/30')    # P1-eth1 / P3-eth0
LINK_P2_P4  = ('10.1.1.9/30',  '10.1.1.10/30')   # P2-eth1 / P4-eth0
LINK_P3_P4  = ('10.1.1.13/30', '10.1.1.14/30')   # P3-eth1 / P4-eth1
LINK_P1_P4  = ('10.1.1.17/30', '10.1.1.18/30')   # P1-eth2 / P4-eth2
LINK_P2_P3  = ('10.1.1.21/30', '10.1.1.22/30')   # P2-eth2 / P3-eth2

# Liên kết P-PE
LINK_P1_PE1 = ('10.1.2.1/30',  '10.1.2.2/30')    # P1-eth3 / PE1-eth0
LINK_P3_PE2 = ('10.1.2.5/30',  '10.1.2.6/30')    # P3-eth3 / PE2-eth0
LINK_P4_PE2 = ('10.1.2.9/30',  '10.1.2.10/30')   # P4-eth3 / PE2-eth1
LINK_P2_PE3 = ('10.1.2.13/30', '10.1.2.14/30')   # P2-eth3 / PE3-eth0
LINK_P4_PE3 = ('10.1.2.17/30', '10.1.2.18/30')   # P4-eth4 / PE3-eth1

# ══════════════════════════════════════════════════════════════════════════════
# PE-CE LINKS
# Dải 10.2.x.x/30
# ══════════════════════════════════════════════════════════════════════════════
LINK_PE1_CE1 = ('10.2.1.1/30', '10.2.1.2/30')    # PE1-eth1 / CE1-eth0
LINK_PE2_CE2 = ('10.2.2.1/30', '10.2.2.2/30')    # PE2-eth2 / CE2-eth0
LINK_PE3_CE3 = ('10.2.3.1/30', '10.2.3.2/30')    # PE3-eth2 / CE3-eth0

# ══════════════════════════════════════════════════════════════════════════════
# CHI NHÁNH 1 – FLAT NETWORK
# Dải 192.168.10.x/24
# ══════════════════════════════════════════════════════════════════════════════
CN1_SUBNET   = '192.168.10.0/24'
CN1_GW       = '192.168.10.254'       # CE1 LAN interface
LINK_CE1_SW1 = ('192.168.10.254/24',) # CE1-eth1 làm gateway

CN1_HOSTS = {
    'host1': '192.168.10.1/24',
    'host2': '192.168.10.2/24',
    'host3': '192.168.10.3/24',
    'host4': '192.168.10.4/24',
}

# ══════════════════════════════════════════════════════════════════════════════
# CHI NHÁNH 2 – 3 LỚP (Core-Distribution-Access)
# Dải 192.168.20.x
# ══════════════════════════════════════════════════════════════════════════════
CN2_SUBNET_ADMIN  = '192.168.21.0/24'   # VLAN 10 – Admin
CN2_SUBNET_LAB    = '192.168.22.0/24'   # VLAN 20 – Lab
CN2_SUBNET_GUEST  = '192.168.23.0/24'   # VLAN 30 – Guest

# CE2 -> Core layer link
LINK_CE2_CORE = ('10.2.2.6/30', '10.2.2.5/30')   # CE2-eth1 / CoreSW-eth0

# Inter-VLAN gateway nằm trên DistSW (Linux router)
CN2_GW_ADMIN  = '192.168.21.254'   # DistSW-eth1 (SVI VLAN10)
CN2_GW_LAB    = '192.168.22.254'   # DistSW-eth2 (SVI VLAN20)
CN2_GW_GUEST  = '192.168.23.254'   # DistSW-eth3 (SVI VLAN30)

CN2_HOSTS = {
    'admin1': ('192.168.21.1/24', CN2_GW_ADMIN),
    'admin2': ('192.168.21.2/24', CN2_GW_ADMIN),
    'lab1':   ('192.168.22.1/24', CN2_GW_LAB),
    'lab2':   ('192.168.22.2/24', CN2_GW_LAB),
    'guest1': ('192.168.23.1/24', CN2_GW_GUEST),
    'guest2': ('192.168.23.2/24', CN2_GW_GUEST),
}

# ══════════════════════════════════════════════════════════════════════════════
# CHI NHÁNH 3 – LEAF-SPINE
# Dải 192.168.30.x
# ══════════════════════════════════════════════════════════════════════════════
CN3_SUBNET_WEB = '192.168.31.0/24'   # Leaf1 – Web servers
CN3_SUBNET_DNS = '192.168.32.0/24'   # Leaf2 – DNS servers
CN3_SUBNET_DB  = '192.168.33.0/24'   # Leaf3 – DB servers

# Spine-Leaf underlay (point-to-point /30)
CN3_LINKS = {
    'spine1-leaf1': ('10.3.1.1/30',  '10.3.1.2/30'),
    'spine1-leaf2': ('10.3.1.5/30',  '10.3.1.6/30'),
    'spine1-leaf3': ('10.3.1.9/30',  '10.3.1.10/30'),
    'spine2-leaf1': ('10.3.1.13/30', '10.3.1.14/30'),
    'spine2-leaf2': ('10.3.1.17/30', '10.3.1.18/30'),
    'spine2-leaf3': ('10.3.1.21/30', '10.3.1.22/30'),
}

# CE3 link to Spine
LINK_CE3_SPINE1 = ('10.2.3.6/30', '10.2.3.5/30')   # CE3-eth1 / Spine1-eth0
LINK_CE3_SPINE2 = ('10.2.3.10/30','10.2.3.9/30')   # CE3-eth2 / Spine2-eth0

# Loopback cho Spine và Leaf (dùng làm Router-ID nội bộ CN3)
CN3_LOOPBACK = {
    'spine1': '10.3.0.1/32',
    'spine2': '10.3.0.2/32',
    'leaf1':  '10.3.0.11/32',
    'leaf2':  '10.3.0.12/32',
    'leaf3':  '10.3.0.13/32',
}

# Gateway trên từng Leaf
CN3_GW = {
    'leaf1': '192.168.31.254',   # eth kết nối host web
    'leaf2': '192.168.32.254',   # eth kết nối host dns
    'leaf3': '192.168.33.254',   # eth kết nối host db
}

CN3_HOSTS = {
    'web1': ('192.168.31.1/24', '192.168.31.254'),
    'web2': ('192.168.31.2/24', '192.168.31.254'),
    'dns1': ('192.168.32.1/24', '192.168.32.254'),
    'dns2': ('192.168.32.2/24', '192.168.32.254'),
    'db1':  ('192.168.33.1/24', '192.168.33.254'),
    'db2':  ('192.168.33.2/24', '192.168.33.254'),
}

# ══════════════════════════════════════════════════════════════════════════════
# VPLS / GRE PSEUDOWIRE (L2 Metro Ethernet fallback)
# ══════════════════════════════════════════════════════════════════════════════
# GRE key phân biệt các VPN/customer
VPLS_KEY_CN1_CN3 = 101   # tunnel kết nối CN1 và CN3 qua Metro Ethernet

# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════
def strip_prefix(ip_cidr: str) -> str:
    """Trả về địa chỉ IP không có prefix, vd '10.0.0.1/32' -> '10.0.0.1'."""
    return ip_cidr.split('/')[0]


def peer_ip(ip_cidr: str) -> str:
    """
    Với /30, trả về IP peer.
    vd: '10.1.1.1/30' -> '10.1.1.2', '10.1.1.2/30' -> '10.1.1.1'.
    """
    import ipaddress
    net = ipaddress.ip_interface(ip_cidr).network
    hosts = list(net.hosts())
    addr  = ipaddress.ip_address(ip_cidr.split('/')[0])
    if addr == hosts[0]:
        return str(hosts[1])
    return str(hosts[0])


def print_address_table():
    """In bảng địa chỉ IP ra stdout để debug."""
    print('\n' + '='*70)
    print('  IP ADDRESS PLAN – Metro Ethernet MPLS Project')
    print('='*70)
    print('\n--- Loopback (Router-ID) ---')
    for r, ip in LOOPBACK.items():
        print(f'  {r:<6}  lo  {ip}')
    print('\n--- Backbone P-P Links ---')
    for name, (a, b) in [('P1-P2', LINK_P1_P2), ('P1-P3', LINK_P1_P3),
                          ('P2-P4', LINK_P2_P4), ('P3-P4', LINK_P3_P4),
                          ('P1-P4', LINK_P1_P4), ('P2-P3', LINK_P2_P3)]:
        print(f'  {name:<8}  {a}  <-->  {b}')
    print('\n--- Backbone P-PE Links ---')
    for name, (a, b) in [('P1-PE1', LINK_P1_PE1), ('P3-PE2', LINK_P3_PE2),
                          ('P4-PE2', LINK_P4_PE2), ('P2-PE3', LINK_P2_PE3),
                          ('P4-PE3', LINK_P4_PE3)]:
        print(f'  {name:<8}  {a}  <-->  {b}')
    print('\n--- PE-CE Links ---')
    for name, (a, b) in [('PE1-CE1', LINK_PE1_CE1), ('PE2-CE2', LINK_PE2_CE2),
                          ('PE3-CE3', LINK_PE3_CE3)]:
        print(f'  {name:<8}  {a}  <-->  {b}')
    print('\n--- CN1 Flat LAN (192.168.10.0/24) ---')
    for h, ip in CN1_HOSTS.items():
        print(f'  {h:<8}  {ip}  gw {CN1_GW}')
    print('\n--- CN2 3-Layer VLANs ---')
    for h, (ip, gw) in CN2_HOSTS.items():
        print(f'  {h:<8}  {ip}  gw {gw}')
    print('\n--- CN3 Leaf-Spine ---')
    for h, (ip, gw) in CN3_HOSTS.items():
        print(f'  {h:<8}  {ip}  gw {gw}')
    print('='*70 + '\n')


if __name__ == '__main__':
    print_address_table()
