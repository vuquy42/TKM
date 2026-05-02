#!/usr/bin/env python3
from addressing import CE_PE_METRO_VLAN, CE_PE_ROUTED, CE_PE_ROUTED_VLAN, CN3, METRO_SERVICE
from common import (
    LinuxMPLSRouter,
    RouterCE,
    add_bridge,
    add_ip,
    add_loopback,
    add_route,
    add_vlan_subif,
    set_link_up,
    start_frr,
    vtysh_apply,
    wait_msg,
)


def add_branch3(net):
    net.addHost("CE3", cls=RouterCE, ip=None)
    net.addHost("SP1", cls=LinuxMPLSRouter, ip=None)
    net.addHost("SP2", cls=LinuxMPLSRouter, ip=None)
    net.addHost("LEAF1", cls=LinuxMPLSRouter, ip=None)
    net.addHost("LEAF2", cls=LinuxMPLSRouter, ip=None)
    net.addHost("LEAF3", cls=LinuxMPLSRouter, ip=None)

    # CE3 to spines
    net.addLink(net["CE3"], net["SP1"], intfName1="ce3-eth1", intfName2="sp1-eth0")
    net.addLink(net["CE3"], net["SP2"], intfName1="ce3-eth2", intfName2="sp2-eth0")

    # Full-mesh spine -> leaf
    net.addLink(net["SP1"], net["LEAF1"], intfName1="sp1-eth1", intfName2="leaf1-eth0")
    net.addLink(net["SP2"], net["LEAF1"], intfName1="sp2-eth1", intfName2="leaf1-eth1")
    net.addLink(net["SP1"], net["LEAF2"], intfName1="sp1-eth2", intfName2="leaf2-eth0")
    net.addLink(net["SP2"], net["LEAF2"], intfName1="sp2-eth2", intfName2="leaf2-eth1")
    net.addLink(net["SP1"], net["LEAF3"], intfName1="sp1-eth3", intfName2="leaf3-eth0")
    net.addLink(net["SP2"], net["LEAF3"], intfName1="sp2-eth3", intfName2="leaf3-eth1")

    # Servers
    for name in ["web1", "web2", "dns1", "dns2", "db1", "db2"]:
        net.addHost(name, ip=None)

    net.addLink(net["LEAF1"], net["web1"], intfName1="leaf1-eth2", intfName2="web1-eth0")
    net.addLink(net["LEAF1"], net["web2"], intfName1="leaf1-eth3", intfName2="web2-eth0")
    net.addLink(net["LEAF2"], net["dns1"], intfName1="leaf2-eth2", intfName2="dns1-eth0")
    net.addLink(net["LEAF2"], net["dns2"], intfName1="leaf2-eth3", intfName2="dns2-eth0")
    net.addLink(net["LEAF3"], net["db1"], intfName1="leaf3-eth2", intfName2="db1-eth0")
    net.addLink(net["LEAF3"], net["db2"], intfName1="leaf3-eth3", intfName2="db2-eth0")


def configure_branch3(net):
    ce3 = net["CE3"]

    # CE3 <-> PE3 trunk
    add_vlan_subif(ce3, "ce3-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE3"]["ce"])
    metro_if = add_vlan_subif(ce3, "ce3-eth0", CE_PE_METRO_VLAN)
    add_bridge(ce3, "br-metro-ce3", [metro_if], METRO_SERVICE["CE3_BR"])

    # Fabric loopbacks
    for node_name, lo in CN3["loopbacks"].items():
        add_loopback(net[node_name], lo)

    # Fabric p2p addressing
    for a, ia, ipa, b, ib, ipb in CN3["fabric"]:
        add_ip(net[a], ia, ipa)
        add_ip(net[b], ib, ipb)
        set_link_up(net[a], ia)
        set_link_up(net[b], ib)

    # Leaf server bridges
    add_bridge(net["LEAF1"], "br-web", ["leaf1-eth2", "leaf1-eth3"], CN3["web_gw"])
    add_bridge(net["LEAF2"], "br-dns", ["leaf2-eth2", "leaf2-eth3"], CN3["dns_gw"])
    add_bridge(net["LEAF3"], "br-db", ["leaf3-eth2", "leaf3-eth3"], CN3["db_gw"])

    # Host IP
    for h, ip in CN3["hosts"].items():
        net[h].cmd(f"ip addr flush dev {h}-eth0")
        net[h].cmd(f"ip addr add {ip} dev {h}-eth0")
        net[h].cmd(f"ip link set dev {h}-eth0 up")
        net[h].cmd("ip route flush root 0/0")

    net["web1"].cmd("ip route add default via 10.3.10.1")
    net["web2"].cmd("ip route add default via 10.3.10.1")
    net["dns1"].cmd("ip route add default via 10.3.20.1")
    net["dns2"].cmd("ip route add default via 10.3.20.1")
    net["db1"].cmd("ip route add default via 10.3.30.1")
    net["db2"].cmd("ip route add default via 10.3.30.1")

    # Static routes on CE3
    add_route(ce3, "default", via="172.16.3.2", dev="ce3-eth0.10")
    add_route(ce3, "10.1.10.0/24", via="10.13.100.1", dev="br-metro-ce3")

    # FRR for branch3 fabric
    for r in ["CE3", "SP1", "SP2", "LEAF1", "LEAF2", "LEAF3"]:
        daemons = ("zebra", "ospfd", "staticd") if r == "CE3" else ("zebra", "ospfd")
        start_frr(net[r], daemons=daemons)

    _apply_ospf(net)
    wait_msg(10, "Waiting for branch3 OSPF convergence")


def _apply_ospf(net):
    def cfg_router(node_name, rid, p2p_ifaces, passive_ifaces, networks, extra=None):
        lines = []
        for ifn in p2p_ifaces:
            lines += [f"interface {ifn}", " ip ospf network point-to-point", "exit"]
        lines += ["router ospf", f" ospf router-id {rid}", " maximum-paths 16"]
        for p in passive_ifaces:
            lines.append(f" passive-interface {p}")
        for nw in networks:
            lines.append(f" network {nw} area 0")
        if extra:
            lines.append(extra)
        lines.append("exit")
        vtysh_apply(net[node_name], lines)

    cfg_router(
        "CE3",
        "10.253.0.1",
        ["ce3-eth1", "ce3-eth2"],
        ["lo", "br-metro-ce3"],
        ["10.253.0.1/32", "10.3.0.0/30", "10.3.0.4/30"],
        " redistribute static",
    )

    cfg_router(
        "SP1",
        "10.253.0.11",
        ["sp1-eth0", "sp1-eth1", "sp1-eth2", "sp1-eth3"],
        ["lo"],
        ["10.253.0.11/32", "10.3.0.0/30", "10.3.1.0/30", "10.3.1.8/30", "10.3.1.16/30"],
    )

    cfg_router(
        "SP2",
        "10.253.0.12",
        ["sp2-eth0", "sp2-eth1", "sp2-eth2", "sp2-eth3"],
        ["lo"],
        ["10.253.0.12/32", "10.3.0.4/30", "10.3.1.4/30", "10.3.1.12/30", "10.3.1.20/30"],
    )

    cfg_router(
        "LEAF1",
        "10.253.0.21",
        ["leaf1-eth0", "leaf1-eth1"],
        ["lo", "br-web"],
        ["10.253.0.21/32", "10.3.1.0/30", "10.3.1.4/30", "10.3.10.0/24"],
    )

    cfg_router(
        "LEAF2",
        "10.253.0.22",
        ["leaf2-eth0", "leaf2-eth1"],
        ["lo", "br-dns"],
        ["10.253.0.22/32", "10.3.1.8/30", "10.3.1.12/30", "10.3.20.0/24"],
    )

    cfg_router(
        "LEAF3",
        "10.253.0.23",
        ["leaf3-eth0", "leaf3-eth1"],
        ["lo", "br-db"],
        ["10.253.0.23/32", "10.3.1.16/30", "10.3.1.20/30", "10.3.30.0/24"],
    )