#!/usr/bin/env python3
from mininet.log import info

from addressing import (
    BACKBONE_LINKS,
    BACKBONE_LOOPBACKS,
    BACKBONE_MTU,
    CE_PE_METRO_VLAN,
    CE_PE_ROUTED,
    CE_PE_ROUTED_VLAN,
    METRO_SERVICE,
    PE_STATIC_ROUTES,
)
from common import (
    RouterP,
    RouterPE,
    add_bridge,
    add_ip,
    add_loopback,
    add_vlan_subif,
    enable_mpls_on_interfaces,
    interface_exists,
    set_link_up,
    start_frr,
    vtysh_apply,
    wait_msg,
)


def add_backbone(net):
    for name in ["P1", "P2", "P3", "P4"]:
        net.addHost(name, cls=RouterP, ip=None)

    for name in ["PE1", "PE2", "PE3"]:
        net.addHost(name, cls=RouterPE, ip=None)

    # PE-CE trunks
    net.addLink(net["CE1"], net["PE1"], intfName1="ce1-eth0", intfName2="pe1-eth0")
    net.addLink(net["CE2"], net["PE2"], intfName1="ce2-eth0", intfName2="pe2-eth0")
    net.addLink(net["CE3"], net["PE3"], intfName1="ce3-eth0", intfName2="pe3-eth0")

    # Backbone physical links
    link_pairs = [
        ("P1", "P2", "p1-eth0", "p2-eth0"),
        ("P1", "P3", "p1-eth1", "p3-eth0"),
        ("P2", "P4", "p2-eth1", "p4-eth0"),
        ("P3", "P4", "p3-eth1", "p4-eth1"),
        ("P2", "P3", "p2-eth2", "p3-eth2"),
        ("PE1", "P1", "pe1-eth1", "p1-eth2"),
        ("PE1", "P3", "pe1-eth2", "p3-eth3"),
        ("PE2", "P3", "pe2-eth1", "p3-eth4"),
        ("PE2", "P4", "pe2-eth2", "p4-eth2"),
        ("PE3", "P2", "pe3-eth1", "p2-eth3"),
        ("PE3", "P4", "pe3-eth2", "p4-eth3"),
    ]
    for a, b, ia, ib in link_pairs:
        net.addLink(net[a], net[b], intfName1=ia, intfName2=ib)


def _router_networks(name):
    return [x[2] for x in BACKBONE_LINKS if x[0] == name] + [x[5] for x in BACKBONE_LINKS if x[3] == name]


def _router_ifaces(name):
    return [x[1] for x in BACKBONE_LINKS if x[0] == name] + [x[4] for x in BACKBONE_LINKS if x[3] == name]


def configure_backbone(net):
    info("*** Configuring backbone interfaces and loopbacks\n")

    for rname, lo in BACKBONE_LOOPBACKS.items():
        node = net[rname]
        add_loopback(node, lo)

    for a, ia, ipa, b, ib, ipb in BACKBONE_LINKS:
        add_ip(net[a], ia, ipa)
        add_ip(net[b], ib, ipb)
        set_link_up(net[a], ia, BACKBONE_MTU)
        set_link_up(net[b], ib, BACKBONE_MTU)

    for rname in ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3"]:
        enable_mpls_on_interfaces(net[rname], _router_ifaces(rname))

    info("*** Creating PE CE subinterfaces\n")
    pe1_routed = add_vlan_subif(net["PE1"], "pe1-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE1"]["pe"])
    pe2_routed = add_vlan_subif(net["PE2"], "pe2-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE2"]["pe"])
    pe3_routed = add_vlan_subif(net["PE3"], "pe3-eth0", CE_PE_ROUTED_VLAN, CE_PE_ROUTED["CE3"]["pe"])

    pe1_metro = add_vlan_subif(net["PE1"], "pe1-eth0", CE_PE_METRO_VLAN)
    pe3_metro = add_vlan_subif(net["PE3"], "pe3-eth0", CE_PE_METRO_VLAN)

    add_bridge(net["PE1"], "br-metro", [pe1_metro])
    add_bridge(net["PE3"], "br-metro", [pe3_metro])

    info("*** Starting FRR on P/PE\n")
    for r in ["P1", "P2", "P3", "P4"]:
        start_frr(net[r], daemons=("zebra", "ospfd", "ldpd"))
    for r in ["PE1", "PE2", "PE3"]:
        start_frr(net[r], daemons=("zebra", "ospfd", "ldpd", "staticd"))

    info("*** Applying OSPF/LDP configs on backbone\n")
    for rname in ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3"]:
        node = net[rname]
        rid = BACKBONE_LOOPBACKS[rname].split("/")[0]
        networks = [BACKBONE_LOOPBACKS[rname]] + _router_networks(rname)
        ifaces = _router_ifaces(rname)

        lines = []
        for ifn in ifaces:
            lines += [f"interface {ifn}", " ip ospf network point-to-point", "exit"]

        lines += [
            "router ospf",
            f" ospf router-id {rid}",
            " passive-interface lo",
            " maximum-paths 16",
        ]
        for nw in networks:
            lines.append(f" network {nw} area 0")
        lines.append("exit")

        lines += [
            "mpls ldp",
            f" router-id {rid}",
            " ordered-control",
            " address-family ipv4",
            f"  discovery transport-address {rid}",
        ]
        for ifn in ifaces:
            lines.append(f"  interface {ifn}")
        lines += [" exit-address-family", "exit"]

        vtysh_apply(node, lines)

    info("*** Installing static customer routes on PE\n")
    for pe_name, routes in PE_STATIC_ROUTES.items():
        lines = []
        for prefix, nh in routes.items():
            lines.append(f"ip route {prefix} {nh}")
        vtysh_apply(net[pe_name], lines)

    info("*** Configuring FRR VPLS on PE1/PE3\n")
    pwid = METRO_SERVICE["PW_ID"]
    pe1_lo = BACKBONE_LOOPBACKS["PE1"].split("/")[0]
    pe3_lo = BACKBONE_LOOPBACKS["PE3"].split("/")[0]

    vtysh_apply(
        net["PE1"],
        [
            f"l2vpn {METRO_SERVICE['NAME']} type vpls",
            " bridge br-metro",
            f" member interface {pe1_metro}",
            " member pseudowire mpw0",
            f"  neighbor lsr-id {pe3_lo}",
            f"  pw-id {pwid}",
            " exit",
            "exit",
        ],
    )

    vtysh_apply(
        net["PE3"],
        [
            f"l2vpn {METRO_SERVICE['NAME']} type vpls",
            " bridge br-metro",
            f" member interface {pe3_metro}",
            " member pseudowire mpw0",
            f"  neighbor lsr-id {pe1_lo}",
            f"  pw-id {pwid}",
            " exit",
            "exit",
        ],
    )

    wait_msg(15, "Waiting for OSPF/LDP/VPLS to settle")

    if not interface_exists(net["PE1"], "mpw0") or not interface_exists(net["PE3"], "mpw0"):
        info("*** FRR pseudowire not visible, enabling VXLAN fallback for Metro L2 service\n")
        _setup_vxlan_fallback(net)


def _setup_vxlan_fallback(net):
    pe1 = net["PE1"]
    pe3 = net["PE3"]
    pe1_lo = BACKBONE_LOOPBACKS["PE1"].split("/")[0]
    pe3_lo = BACKBONE_LOOPBACKS["PE3"].split("/")[0]

    pe1.cmd("ip link add vxmetro100 type vxlan id 100 dstport 4789 local 10.255.0.11 nolearning 2>/dev/null || true")
    pe1.cmd("ip link set vxmetro100 up")
    pe1.cmd("ip link set vxmetro100 master br-metro")
    pe1.cmd(f"bridge fdb append 00:00:00:00:00:00 dev vxmetro100 dst {pe3_lo} 2>/dev/null || true")

    pe3.cmd("ip link add vxmetro100 type vxlan id 100 dstport 4789 local 10.255.0.13 nolearning 2>/dev/null || true")
    pe3.cmd("ip link set vxmetro100 up")
    pe3.cmd("ip link set vxmetro100 master br-metro")
    pe3.cmd(f"bridge fdb append 00:00:00:00:00:00 dev vxmetro100 dst {pe1_lo} 2>/dev/null || true")