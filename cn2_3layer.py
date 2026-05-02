#!/usr/bin/env python3
from mininet.node import OVSKernelSwitch

from addressing import CE_PE_ROUTED, CE_PE_ROUTED_VLAN, CN2
from common import RouterCE, LinuxMPLSRouter, add_bridge, add_ip, add_route, add_vlan_subif, ovs_access, ovs_trunk


def add_branch2(net):
    net.addHost("CE2", cls=RouterCE, ip=None)
    net.addHost("CN2D1", cls=LinuxMPLSRouter, ip=None)
    net.addHost("CN2D2", cls=LinuxMPLSRouter, ip=None)

    net.addSwitch("cn2core1", cls=OVSKernelSwitch)
    net.addSwitch("cn2core2", cls=OVSKernelSwitch)
    net.addSwitch("cn2acc1", cls=OVSKernelSwitch)
    net.addSwitch("cn2acc2", cls=OVSKernelSwitch)
    net.addSwitch("cn2acc3", cls=OVSKernelSwitch)

    # Core transit
    net.addLink(net["CE2"], net["cn2core1"], intfName1="ce2-eth1", intfName2="cn2core1-u1")
    net.addLink(net["cn2core1"], net["cn2core2"], intfName1="cn2core1-u2", intfName2="cn2core2-u1")
    net.addLink(net["CN2D1"], net["cn2core1"], intfName1="cn2d1-eth0", intfName2="cn2core1-d1")
    net.addLink(net["CN2D2"], net["cn2core2"], intfName1="cn2d2-eth0", intfName2="cn2core2-d2")

    # Dist -> Access
    net.addLink(net["CN2D1"], net["cn2acc1"], intfName1="cn2d1-eth1", intfName2="cn2acc1-u1")
    net.addLink(net["CN2D1"], net["cn2acc2"], intfName1="cn2d1-eth2", intfName2="cn2acc2-u1")
    net.addLink(net["CN2D2"], net["cn2acc3"], intfName1="cn2d2-eth1", intfName2="cn2acc3-u1")

    # Hosts
    for name, sw, p in [
        ("admin1", "cn2acc1", "h1"),
        ("admin2", "cn2acc1", "h2"),
        ("lab1", "cn2acc2", "h1"),
        ("lab2", "cn2acc2", "h2"),
        ("guest1", "cn2acc3", "h1"),
        ("guest2", "cn2acc3", "h2"),
    ]:
        net.addHost(name, ip=None)
        net.addLink(net[name], net[sw], intfName1=f"{name}-eth0", intfName2=f"{sw}-{p}")


def configure_branch2(net):
    ce2 = net["CE2"]
    d1 = net["CN2D1"]
    d2 = net["CN2D2"]

    add_vlan_subif(ce2, "ce2-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE2"]["ce"])
    add_ip(ce2, "ce2-eth1", CN2["transit_ce2"])

    add_ip(d1, "cn2d1-eth0", CN2["dist1_transit"])
    add_ip(d2, "cn2d2-eth0", CN2["dist2_transit"])

    # Distribution SVI via VLAN sub-interfaces
    d1_v10 = add_vlan_subif(d1, "cn2d1-eth1", 10)
    d1_v20 = add_vlan_subif(d1, "cn2d1-eth2", 20)
    d2_v30 = add_vlan_subif(d2, "cn2d2-eth1", 30)

    add_bridge(d1, "br-admin", [d1_v10], CN2["admin_gw"])
    add_bridge(d1, "br-lab", [d1_v20], CN2["lab_gw"])
    add_bridge(d2, "br-guest", [d2_v30], CN2["guest_gw"])

    # Access switch VLANs
    ovs_trunk(net["cn2acc1"], "cn2acc1-u1", [10])
    ovs_access(net["cn2acc1"], "cn2acc1-h1", 10)
    ovs_access(net["cn2acc1"], "cn2acc1-h2", 10)

    ovs_trunk(net["cn2acc2"], "cn2acc2-u1", [20])
    ovs_access(net["cn2acc2"], "cn2acc2-h1", 20)
    ovs_access(net["cn2acc2"], "cn2acc2-h2", 20)

    ovs_trunk(net["cn2acc3"], "cn2acc3-u1", [30])
    ovs_access(net["cn2acc3"], "cn2acc3-h1", 30)
    ovs_access(net["cn2acc3"], "cn2acc3-h2", 30)

    # Host IP / GW
    for h, ip in CN2["hosts"].items():
        net[h].cmd(f"ip addr flush dev {h}-eth0")
        net[h].cmd(f"ip addr add {ip} dev {h}-eth0")
        net[h].cmd(f"ip link set dev {h}-eth0 up")
        net[h].cmd("ip route flush root 0/0")

    net["admin1"].cmd("ip route add default via 10.2.10.1")
    net["admin2"].cmd("ip route add default via 10.2.10.1")
    net["lab1"].cmd("ip route add default via 10.2.20.1")
    net["lab2"].cmd("ip route add default via 10.2.20.1")
    net["guest1"].cmd("ip route add default via 10.2.30.1")
    net["guest2"].cmd("ip route add default via 10.2.30.1")

    # Routes inside branch2
    add_route(d1, "default", via="10.2.0.1")
    add_route(d2, "default", via="10.2.0.1")

    add_route(ce2, "10.2.10.0/24", via="10.2.0.11", dev="ce2-eth1")
    add_route(ce2, "10.2.20.0/24", via="10.2.0.11", dev="ce2-eth1")
    add_route(ce2, "10.2.30.0/24", via="10.2.0.12", dev="ce2-eth1")

    # Remote branches via PE2
    add_route(ce2, "default", via="172.16.2.2", dev="ce2-eth0.10")