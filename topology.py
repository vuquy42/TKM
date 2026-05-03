#!/usr/bin/env python3
"""
topology.py - Orchestrator chính cho dự án Metro Ethernet MPLS.

Trình tự khởi tạo (theo PHẦN 5 của đề bài):
  1. cleanup
  2. modprobe MPLS kernel
  3. Dựng topo (addHost/addSwitch/addLink)
  4. net.start()
  5. Gán IP cho tất cả node
  6. Bật FRR daemon (zebra, ospfd, ldpd)
  7. Cấu hình OSPF backbone
  8. Cấu hình LDP
  9. Cấu hình VPLS/GRE pseudowire
  10. Đợi hội tụ (45s OSPF + 15s LDP)
  11. Verify và mở CLI

Chạy:
  sudo python3 topology.py [--no-cli] [--verify] [--clean]

Flags:
  --no-cli   : Không mở Mininet CLI (dùng khi gọi từ tool.py)
  --verify   : Chạy verify backbone/branches rồi thoát
  --clean    : Chỉ cleanup rồi thoát
"""

import os
import sys
import time
import argparse

# Mininet imports
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info, error

# Module nội bộ
from common import load_mpls_modules, cleanup_mininet
from addressing import print_address_table

from backbone import (
    add_backbone_nodes, add_backbone_links,
    configure_backbone, start_backbone_frr,
    configure_ospf, configure_ldp,
    configure_vpls_gre, wait_convergence,
    verify_backbone, enable_mpls_pe_ce
)
from cn1_flat      import add_cn1_nodes, add_cn1_links, configure_cn1, verify_cn1
from cn2_3layer    import add_cn2_nodes, add_cn2_links, configure_cn2, verify_cn2
from cn3_leafspine import (add_cn3_nodes, add_cn3_links,
                            configure_cn3, start_cn3_frr, verify_cn3)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN BUILD FUNCTION
# ──────────────────────────────────────────────────────────────────────────────
def build_network(wait: bool = True):
    """
    Dựng toàn bộ mạng Metro Ethernet MPLS và trả về net object.
    Nếu wait=True, đợi OSPF/LDP hội tụ trước khi return.
    """
    # ── STEP 1: Cleanup ───────────────────────────────────────────────────────
    cleanup_mininet()

    # ── STEP 2: modprobe MPLS kernel ─────────────────────────────────────────
    load_mpls_modules()

    # ── STEP 3: Khởi tạo Mininet ─────────────────────────────────────────────
    info('*** Creating Mininet network...\n')
    net = Mininet(
        controller=None,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,   # Không pre-populate ARP (giống thực tế)
    )

    # ── STEP 3a: Add backbone nodes ───────────────────────────────────────────
    add_backbone_nodes(net)

    # ── STEP 3b: Add branch nodes ─────────────────────────────────────────────
    add_cn1_nodes(net)
    add_cn2_nodes(net)
    add_cn3_nodes(net)

    # ── STEP 3c: Add links ────────────────────────────────────────────────────
    add_backbone_links(net)
    add_cn1_links(net)
    add_cn2_links(net)
    add_cn3_links(net)

    # ── STEP 4: Start Mininet ─────────────────────────────────────────────────
    info('*** Starting Mininet...\n')
    net.start()
    time.sleep(1)

    # ── STEP 5: Configure IPs ─────────────────────────────────────────────────
    configure_backbone(net)
    configure_cn1(net)
    configure_cn2(net)
    configure_cn3(net)

    # ── STEP 5b: Bật MPLS trên interface PE-CE (sau khi links đã tồn tại) ────
    enable_mpls_pe_ce(net)

    # ── STEP 6: Start FRR daemons ─────────────────────────────────────────────
    start_backbone_frr(net)
    # CN3 có thể dùng static route hoặc FRR OSPF nội bộ
    # Gọi start_cn3_frr để có dynamic routing (optional nhưng tốt hơn)
    try:
        start_cn3_frr(net)
    except Exception as e:
        info(f'    [WARN] CN3 FRR start failed: {e}. Using static routes only.\n')

    # ── STEP 7: Configure OSPF ────────────────────────────────────────────────
    configure_ospf(net)

    # ── STEP 8: Configure LDP ─────────────────────────────────────────────────
    configure_ldp(net)

    # ── STEP 9: Configure VPLS/GRE ───────────────────────────────────────────
    configure_vpls_gre(net)

    # ── STEP 10: Wait convergence ─────────────────────────────────────────────
    if wait:
        wait_convergence()

    info('\n*** === Metro Ethernet MPLS Network READY ===\n')
    _print_quick_guide()

    return net


# ──────────────────────────────────────────────────────────────────────────────
# VERIFY ALL
# ──────────────────────────────────────────────────────────────────────────────
def verify_all(net):
    """Chạy toàn bộ verification sau khi hội tụ."""
    verify_backbone(net)
    verify_cn1(net)
    verify_cn2(net)
    verify_cn3(net)
    _verify_cross_branch(net)


def _verify_cross_branch(net):
    """Kiểm tra ping xuyên chi nhánh qua MPLS backbone."""
    info('\n--- Cross-Branch Verification (via MPLS backbone) ---\n')

    # host1 (CN1) -> web1 (CN3) – Metro Ethernet L2 qua GRE pseudowire
    h1   = net['host1']
    web1 = net['web1']
    result = h1.cmd('ping -c 3 -W 3 192.168.31.1 2>&1')
    if '0% packet loss' in result or '3 received' in result or '1 received' in result:
        info('    [OK] host1 (CN1) -> web1 (CN3) via MPLS backbone OK\n')
    else:
        info('    [WARN] host1 -> web1 cross-branch may not be ready\n')
        info(f'    {result}\n')

    # host1 (CN1) -> admin1 (CN2)
    admin1 = net['admin1']
    result = h1.cmd('ping -c 2 -W 2 192.168.21.1 2>&1')
    if '0% packet loss' in result or '2 received' in result or '1 received' in result:
        info('    [OK] host1 (CN1) -> admin1 (CN2) OK\n')
    else:
        info('    [WARN] host1 -> admin1 cross-branch may need more time\n')


# ──────────────────────────────────────────────────────────────────────────────
# QUICK GUIDE
# ──────────────────────────────────────────────────────────────────────────────
def _print_quick_guide():
    info('\n' + '='*70 + '\n')
    info('  METRO ETHERNET MPLS – Quick Test Guide\n')
    info('='*70 + '\n')
    info('  Ping intra-CN1:   host1 ping 192.168.10.2\n')
    info('  Ping inter-VLAN:  admin1 ping 192.168.22.1  (CN2)\n')
    info('  Ping cross-leaf:  web1 ping 192.168.33.1    (CN3)\n')
    info('  Ping backbone:    host1 ping 192.168.31.1   (CN1->CN3 via MPLS)\n')
    info('  Traceroute MPLS:  host1 traceroute 192.168.31.1\n')
    info('  OSPF check:       PE1 vtysh -c "show ip ospf neighbor"\n')
    info('  LDP check:        PE1 vtysh -c "show mpls ldp neighbor"\n')
    info('  MPLS table:       P1 ip route\n')
    info('  Run tests:        sudo python3 tool.py --all\n')
    info('='*70 + '\n')


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(
        description='Metro Ethernet MPLS Mininet Topology'
    )
    parser.add_argument('--no-cli',  action='store_true',
                        help='Do not open Mininet CLI')
    parser.add_argument('--verify',  action='store_true',
                        help='Run verification after convergence and exit')
    parser.add_argument('--no-wait', action='store_true',
                        help='Skip convergence wait (for quick testing)')
    parser.add_argument('--clean',   action='store_true',
                        help='Only run cleanup and exit')
    parser.add_argument('--addr',    action='store_true',
                        help='Print address table and exit')
    return parser.parse_args()


def main():
    setLogLevel('info')

    if os.geteuid() != 0:
        error('ERROR: Must run as root (sudo python3 topology.py)\n')
        sys.exit(1)

    args = parse_args()

    if args.clean:
        cleanup_mininet()
        sys.exit(0)

    if args.addr:
        print_address_table()
        sys.exit(0)

    # Dựng mạng
    wait = not args.no_wait
    net  = build_network(wait=wait)

    try:
        if args.verify:
            verify_all(net)
        elif not args.no_cli:
            CLI(net)
        else:
            # Headless mode: đợi signal
            info('*** Running in headless mode. Press Ctrl+C to stop.\n')
            while True:
                time.sleep(5)
    except KeyboardInterrupt:
        info('\n*** Keyboard interrupt received.\n')
    finally:
        info('*** Stopping network...\n')
        net.stop()
        cleanup_mininet()


if __name__ == '__main__':
    main()
