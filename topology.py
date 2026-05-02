#!/usr/bin/env python3
import argparse

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch

import backbone
import cn1_flat
import cn2_3layer
import cn3_leafspine

from common import cleanup_all, ensure_netns_links, load_mpls_kernel, wait_msg


def build_network():
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)

    # Add branches first (CE + LAN)
    cn1_flat.add_branch1(net)
    cn2_3layer.add_branch2(net)
    cn3_leafspine.add_branch3(net)

    # Add ISP backbone after CE nodes exist
    backbone.add_backbone(net)
    return net


def configure_network(net):
    ensure_netns_links(net)

    info("*** Configuring branch LANs\n")
    cn1_flat.configure_branch1(net)
    cn2_3layer.configure_branch2(net)
    cn3_leafspine.configure_branch3(net)

    info("*** Configuring MPLS backbone\n")
    backbone.configure_backbone(net)

    wait_msg(20, "Final convergence/warm-up wait")

    info("*** Warm-up ping to trigger ARP/MAC learning\n")
    net["host1"].cmd("ping -c 2 10.3.10.11 >/dev/null 2>&1 || true")
    net["admin1"].cmd("ping -c 2 10.3.30.31 >/dev/null 2>&1 || true")
    net["host1"].cmd("ping -c 2 10.2.10.11 >/dev/null 2>&1 || true")


def main():
    parser = argparse.ArgumentParser(description="Metro Ethernet MPLS on Mininet")
    parser.add_argument("--no-cli", action="store_true", help="Build and configure only, then exit")
    args = parser.parse_args()

    cleanup_all()
    load_mpls_kernel()

    net = build_network()
    net.start()

    try:
        configure_network(net)

        info("\n*** Topology ready\n")
        info("*** Suggested tests inside Mininet CLI:\n")
        info("mininet> host1 ping -c 4 web1\n")
        info("mininet> admin1 traceroute -n db1\n")
        info("mininet> PE1 vtysh -c 'show ip ospf neighbor'\n")
        info("mininet> PE1 vtysh -c 'show mpls ldp neighbor'\n")
        info("mininet> P1 vtysh -c 'show mpls table'\n")

        if not args.no_cli:
            CLI(net)
    finally:
        net.stop()
        cleanup_all()


if __name__ == "__main__":
    setLogLevel("info")
    main()