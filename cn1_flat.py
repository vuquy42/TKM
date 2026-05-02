#!/usr/bin/env python3
from mininet.node import OVSKernelSwitch

from addressing import CE_PE_METRO_VLAN, CE_PE_ROUTED, CE_PE_ROUTED_VLAN, CN1, METRO_SERVICE
from common import RouterCE, add_bridge, add_ip, add_route, add_vlan_subif


def add_branch1(net):
    net.addHost("CE1", cls=RouterCE, ip=None)
    net.addSwitch("cn1sw1", cls=OVSKernelSwitch)

    net.addLink(net["CE1"], net["cn1sw1"], intfName1="ce1-eth1", intfName2="cn1sw1-u1")

    for idx in range(1, 5):
        h = f"host{idx}"
        net.addHost(h, ip=None)
        net.addLink(net[h], net["cn1sw1"], intfName1=f"{h}-eth0", intfName2=f"cn1sw1-{h}")


def configure_branch1(net):
    ce1 = net["CE1"]

    # CE1 <-> PE1 trunk sub-interfaces
    add_vlan_subif(ce1, "ce1-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE1"]["ce"])
    metro_if = add_vlan_subif(ce1, "ce1-eth0", CE_PE_METRO_VLAN)
    add_bridge(ce1, "br-metro-ce1", [metro_if], METRO_SERVICE["CE1_BR"])

    # Flat LAN
    add_ip(ce1, "ce1-eth1", CN1["gateway"])

    for h, ip in CN1["hosts"].items():
        net[h].cmd(f"ip addr flush dev {h}-eth0")
        net[h].cmd(f"ip addr add {ip} dev {h}-eth0")
        net[h].cmd(f"ip link set dev {h}-eth0 up")
        net[h].cmd("ip route flush root 0/0")
        net[h].cmd("ip route add default via 10.1.10.1")

    # Routed branch2 through PE1, Metro/VPLS toward branch3 through CE3
    add_route(ce1, "10.2.0.0/24", via="172.16.1.2")
    add_route(ce1, "10.2.10.0/24", via="172.16.1.2")
    add_route(ce1, "10.2.20.0/24", via="172.16.1.2")
    add_route(ce1, "10.2.30.0/24", via="172.16.1.2")

    add_route(ce1, "10.3.10.0/24", via="10.13.100.3", dev="br-metro-ce1")
    add_route(ce1, "10.3.20.0/24", via="10.13.100.3", dev="br-metro-ce1")
    add_route(ce1, "10.3.30.0/24", via="10.13.100.3", dev="br-metro-ce1")