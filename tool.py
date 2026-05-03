#!/usr/bin/env python3
"""
tool.py - Công cụ đo đạc, export CSV/PNG cho dự án Metro Ethernet MPLS.

Chức năng:
  1. Ping test (delay/jitter/loss)
  2. Traceroute – phát hiện MPLS label
  3. Throughput (iperf3)
  4. Stress test (multi-stream iperf)
  5. Export PNG + CSV cho từng loại kết quả

Chạy:
  sudo python3 tool.py --all          # Chạy tất cả test
  sudo python3 tool.py --ping         # Chỉ ping test
  sudo python3 tool.py --traceroute   # Chỉ traceroute
  sudo python3 tool.py --throughput   # Chỉ throughput
  sudo python3 tool.py --stress       # Chỉ stress test
  sudo python3 tool.py --cross        # Cross-branch ping

Lưu ý: Phải chạy SAU KHI topology.py đã khởi động mạng.
        Tool dùng 'ip netns exec' để inject lệnh vào namespace Mininet.
"""

import os
import sys
import re
import csv
import time
import datetime
import subprocess
import argparse

import matplotlib
matplotlib.use('Agg')   # Không cần X11
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOG_FILE = os.path.join(OUTPUT_DIR, 'test_report.log')

# Map node name -> IP đích (dùng để chạy test)
# Các IP này phải khớp với addressing.py
NODE_IP = {
    # CN1
    'host1': '192.168.10.1',
    'host2': '192.168.10.2',
    'host3': '192.168.10.3',
    'host4': '192.168.10.4',
    # CN2
    'admin1': '192.168.21.1',
    'admin2': '192.168.21.2',
    'lab1':   '192.168.22.1',
    'lab2':   '192.168.22.2',
    'guest1': '192.168.23.1',
    'guest2': '192.168.23.2',
    # CN3
    'web1': '192.168.31.1',
    'web2': '192.168.31.2',
    'dns1': '192.168.32.1',
    'dns2': '192.168.32.2',
    'db1':  '192.168.33.1',
    'db2':  '192.168.33.2',
    # Backbone loopbacks
    'PE1': '10.0.0.11',
    'PE2': '10.0.0.12',
    'PE3': '10.0.0.13',
}

# Các cặp test đại diện (src_node, dst_ip, label)
PING_PAIRS = [
    ('host1', '192.168.10.2',  'CN1 intra (host1->host2)'),
    ('host1', '192.168.10.4',  'CN1 intra (host1->host4)'),
    ('admin1','192.168.21.2',  'CN2 intra-VLAN10'),
    ('admin1','192.168.22.1',  'CN2 inter-VLAN (Admin->Lab)'),
    ('web1',  '192.168.33.1',  'CN3 cross-leaf (web1->db1)'),
    ('host1', '192.168.31.1',  'CN1->CN3 via MPLS backbone'),
    ('host1', '192.168.21.1',  'CN1->CN2 via MPLS backbone'),
    ('web1',  '192.168.21.1',  'CN3->CN2 via MPLS backbone'),
]

THROUGHPUT_PAIRS = [
    ('host1', '192.168.10.2',  'CN1 intra'),
    ('admin1','192.168.22.1',  'CN2 inter-VLAN'),
    ('web1',  '192.168.33.1',  'CN3 cross-leaf'),
    ('host1', '192.168.31.1',  'CN1->CN3 MPLS'),
    ('host1', '192.168.21.1',  'CN1->CN2 MPLS'),
]

STRESS_STREAMS = [1, 2, 4, 8, 16]   # Số luồng iperf cho stress test


# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
def log(msg: str):
    ts   = datetime.datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ──────────────────────────────────────────────────────────────────────────────
# EXEC HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def ns_exec(node: str, cmd: str, timeout: int = 30) -> str:
    """Chạy lệnh trong network namespace của node Mininet."""
    full_cmd = f'ip netns exec {node} {cmd}'
    try:
        result = subprocess.run(
            full_cmd, shell=True,
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 'TIMEOUT'
    except Exception as e:
        return f'ERROR: {e}'


def ns_exec_bg(node: str, cmd: str):
    """Chạy lệnh background trong namespace (không block)."""
    full_cmd = f'ip netns exec {node} {cmd} &'
    os.system(full_cmd)


def kill_iperf(node: str):
    """Kill tất cả iperf3 trên node."""
    ns_exec(node, 'killall -9 iperf3 2>/dev/null', timeout=5)


# ──────────────────────────────────────────────────────────────────────────────
# 1. PING TEST (Delay / Jitter / Loss)
# ──────────────────────────────────────────────────────────────────────────────
def parse_ping(output: str) -> dict:
    """
    Parse kết quả ping -s ... -c N.
    Trả về dict: {avg_ms, min_ms, max_ms, mdev_ms, loss_pct}.
    """
    result = {'avg_ms': -1.0, 'min_ms': -1.0, 'max_ms': -1.0,
              'mdev_ms': -1.0, 'loss_pct': 100.0}
    # Packet loss
    m = re.search(r'(\d+)% packet loss', output)
    if m:
        result['loss_pct'] = float(m.group(1))
    # RTT stats
    m = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', output)
    if m:
        result['min_ms']  = float(m.group(1))
        result['avg_ms']  = float(m.group(2))
        result['max_ms']  = float(m.group(3))
        result['mdev_ms'] = float(m.group(4))   # jitter ≈ mdev
    return result


def run_ping_test(count: int = 10) -> list:
    """
    Chạy ping test cho tất cả cặp trong PING_PAIRS.
    Trả về list of dict.
    """
    log('\n=== PING / DELAY / JITTER / LOSS TEST ===')
    results = []
    for src, dst_ip, label in PING_PAIRS:
        log(f'  Pinging {label} ...')
        # count=10 để có mẫu jitter đủ lớn; -i 0.2 để nhanh
        out = ns_exec(src, f'ping -c {count} -i 0.2 -W 2 {dst_ip}', timeout=count*3+5)
        stats = parse_ping(out)
        stats['src']   = src
        stats['dst']   = dst_ip
        stats['label'] = label
        results.append(stats)
        log(f'    avg={stats["avg_ms"]:.2f}ms  jitter={stats["mdev_ms"]:.2f}ms  '
            f'loss={stats["loss_pct"]:.0f}%')
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 2. TRACEROUTE – MPLS LABEL DETECTION
# ──────────────────────────────────────────────────────────────────────────────
def run_traceroute(src: str, dst_ip: str, label: str = '') -> dict:
    """
    Chạy traceroute từ src đến dst_ip.
    Nếu thấy dòng có '[MPLS: Label ...' thì ghi nhận.
    Trả về dict: {hops: list, mpls_labels: list, raw: str}.
    """
    log(f'  Traceroute {label or src+"->"+dst_ip} ...')
    # traceroute -n: không resolve DNS; -m 15: max hops; -w 2: timeout
    out = ns_exec(src, f'traceroute -n -m 15 -w 2 {dst_ip}', timeout=60)
    hops   = []
    labels = []
    for line in out.splitlines():
        # Dạng: ' 1  10.2.1.1  0.123 ms  0.456 ms  0.789 ms'
        m_hop = re.match(r'\s*(\d+)\s+([\d.]+|\*)', line)
        if m_hop:
            hops.append(f"hop{m_hop.group(1)}={m_hop.group(2)}")
        # Dạng MPLS: '[MPLS: Label 16, Exp 0, TTL 1]'
        m_lbl = re.search(r'\[MPLS:\s*Label\s+(\d+)', line)
        if m_lbl:
            labels.append(f"label={m_lbl.group(1)}")
            log(f'    [MPLS] Label detected: {m_lbl.group(1)} at {line.strip()}')
    result = {
        'src': src, 'dst': dst_ip, 'label': label,
        'hops': hops, 'mpls_labels': labels, 'raw': out
    }
    if labels:
        log(f'    MPLS Labels found: {labels}')
    else:
        log('    No MPLS labels in traceroute output (may need kernel traceroute with MPLS support)')
    return result


def run_all_traceroutes() -> list:
    """Traceroute đại diện qua backbone."""
    log('\n=== TRACEROUTE / PATH TEST ===')
    pairs = [
        ('host1', '192.168.31.1', 'CN1->CN3 (MPLS backbone)'),
        ('host1', '192.168.21.1', 'CN1->CN2 (MPLS backbone)'),
        ('web1',  '192.168.10.1', 'CN3->CN1 (reverse)'),
        ('admin1','192.168.33.1', 'CN2->CN3 (MPLS backbone)'),
    ]
    return [run_traceroute(s, d, l) for s, d, l in pairs]


# ──────────────────────────────────────────────────────────────────────────────
# 3. THROUGHPUT (iperf3)
# ──────────────────────────────────────────────────────────────────────────────
def parse_iperf3(output: str) -> float:
    """
    Parse iperf3 sender output.
    Trả về throughput Mbps, hoặc 0.0 nếu không parse được.
    """
    # Dòng sender summary: '... 0.00-10.00 sec  xxx MBytes  xxx Mbits/sec ...'
    # Ưu tiên dòng "sender" nếu có
    for line in reversed(output.splitlines()):
        if 'sender' in line.lower() or 'Mbits/sec' in line:
            m = re.search(r'([\d.]+)\s+Mbits/sec', line)
            if m:
                return float(m.group(1))
    m = re.search(r'([\d.]+)\s+Mbits/sec', output)
    return float(m.group(1)) if m else 0.0


def measure_throughput(src: str, dst_ip: str, duration: int = 10,
                       streams: int = 1) -> float:
    """
    Đo throughput iperf3 từ src -> dst_ip.
    Khởi động iperf3 server trên dst node, client trên src.
    Trả về Mbps.
    """
    kill_iperf(src)
    dst_node = _ip_to_node(dst_ip)
    if dst_node:
        kill_iperf(dst_node)
        # Dùng nohup + & thay vì -D (iperf3 không hỗ trợ -D)
        ns_exec_bg(dst_node, 'nohup iperf3 -s -p 5201 > /tmp/iperf3_server.log 2>&1')
        time.sleep(1.0)   # Chờ server lắng nghe

    out = ns_exec(
        src,
        f'iperf3 -c {dst_ip} -p 5201 -t {duration} -P {streams} -f m 2>&1',
        timeout=duration + 20
    )
    mbps = parse_iperf3(out)

    if dst_node:
        kill_iperf(dst_node)
    return mbps


def run_throughput_test() -> list:
    """Chạy throughput test cho tất cả cặp THROUGHPUT_PAIRS."""
    log('\n=== THROUGHPUT TEST (iperf3, 10s each) ===')
    results = []
    for src, dst_ip, label in THROUGHPUT_PAIRS:
        log(f'  Testing {label} ...')
        mbps = measure_throughput(src, dst_ip, duration=10, streams=1)
        results.append({'src': src, 'dst': dst_ip, 'label': label,
                        'throughput_mbps': mbps})
        log(f'    {label}: {mbps:.2f} Mbps')
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 4. STRESS TEST
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# 4a. FRR / OSPF / LDP STATUS REPORT
# ──────────────────────────────────────────────────────────────────────────────
def run_frr_status_report() -> dict:
    """
    Thu thập trạng thái FRR trên các router backbone.
    Dùng 'ip netns exec <node> vtysh -c "show ..."'.
    Trả về dict {node: {ospf_neighbors, ldp_neighbors, mpls_table}}.
    """
    log('\n=== FRR / OSPF / LDP STATUS REPORT ===')
    report = {}
    nodes  = ['PE1', 'PE2', 'PE3', 'P1', 'P2']

    for node in nodes:
        log(f'  Checking {node}...')
        ospf = ns_exec(node, 'vtysh -c "show ip ospf neighbor" 2>&1', timeout=10)
        ldp  = ns_exec(node, 'vtysh -c "show mpls ldp neighbor" 2>&1', timeout=10)
        mpls = ns_exec(node, 'ip route show table all 2>&1 | head -30', timeout=10)
        report[node] = {
            'ospf_neighbors': ospf,
            'ldp_neighbors':  ldp,
            'mpls_table':     mpls,
        }
        # Log tóm tắt
        ospf_count = len([l for l in ospf.splitlines() if 'Full' in l or 'Up' in l])
        ldp_count  = len([l for l in ldp.splitlines()  if 'OPERATIONAL' in l or 'Established' in l])
        log(f'    OSPF Full neighbors: {ospf_count}')
        log(f'    LDP operational:     {ldp_count}')

    # Export to CSV
    path = os.path.join(OUTPUT_DIR, 'frr_status.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['node', 'ospf_neighbors_raw', 'ldp_neighbors_raw', 'mpls_table_raw'])
        for node, data in report.items():
            w.writerow([node, data['ospf_neighbors'][:500],
                        data['ldp_neighbors'][:500], data['mpls_table'][:500]])
    log(f'  FRR status saved: {path}')
    return report


def run_stress_test(src: str = 'host1', dst_ip: str = '192.168.31.1',
                    duration: int = 8) -> list:
    """
    Tăng dần số luồng iperf3 để xem ảnh hưởng lên throughput, delay, loss.
    Trả về list of dict.
    """
    log(f'\n=== STRESS TEST ({src} -> {dst_ip}) ===')
    results = []
    for streams in STRESS_STREAMS:
        log(f'  Streams={streams} ...')
        mbps = measure_throughput(src, dst_ip, duration=duration, streams=streams)
        # Đo delay/loss song song
        out  = ns_exec(src, f'ping -c 5 -W 1 {dst_ip}', timeout=15)
        stats = parse_ping(out)
        results.append({
            'streams':          streams,
            'throughput_mbps':  mbps,
            'avg_delay_ms':     stats['avg_ms'],
            'jitter_ms':        stats['mdev_ms'],
            'loss_pct':         stats['loss_pct'],
        })
        log(f'    streams={streams}: {mbps:.2f} Mbps, '
            f'delay={stats["avg_ms"]:.2f}ms, '
            f'jitter={stats["mdev_ms"]:.2f}ms, '
            f'loss={stats["loss_pct"]:.0f}%')
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 5. EXPORT PNG
# ──────────────────────────────────────────────────────────────────────────────
def _bar_chart(labels, values, ylabel, title, filename, color='#4a9eff', unit=''):
    """Vẽ biểu đồ cột và lưu PNG."""
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')
    x = range(len(labels))
    bars = ax.bar(x, values, color=color, edgecolor='white', linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha='right', fontsize=8, color='white')
    ax.set_ylabel(f'{ylabel} ({unit})', color='white')
    ax.set_title(title, color='#FF6B35', fontsize=11, fontweight='bold')
    ax.tick_params(colors='white')
    ax.spines['bottom'].set_color('#555')
    ax.spines['left'].set_color('#555')
    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(False)
    # Ghi giá trị trên đầu cột
    for bar, val in zip(bars, values):
        if val >= 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01*max(values+[1]),
                    f'{val:.2f}', ha='center', va='bottom', fontsize=8, color='white')
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, filename)
    plt.savefig(path, dpi=120, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()
    log(f'  Saved chart: {path}')
    return path


def export_ping_charts(ping_results: list):
    """Export PNG cho delay, jitter, loss."""
    labels = [r['label'] for r in ping_results]
    avgs   = [max(r['avg_ms'],  0) for r in ping_results]
    jits   = [max(r['mdev_ms'], 0) for r in ping_results]
    losses = [r['loss_pct']        for r in ping_results]

    _bar_chart(labels, avgs,   'Avg Delay',   'Delay Comparison (Ping)',      'delay_comparison.png',  '#4a9eff', 'ms')
    _bar_chart(labels, jits,   'Jitter',      'Jitter Comparison (Ping)',     'jitter_comparison.png', '#2ECC71', 'ms')
    _bar_chart(labels, losses, 'Packet Loss', 'Packet Loss Comparison',       'loss_comparison.png',   '#E63946', '%')


def export_throughput_chart(tp_results: list):
    """Export PNG cho throughput."""
    labels = [r['label']            for r in tp_results]
    values = [r['throughput_mbps']  for r in tp_results]
    _bar_chart(labels, values, 'Throughput', 'Throughput Comparison (iperf3)',
               'throughput_comparison.png', '#FF6B35', 'Mbps')


def export_stress_chart(stress_results: list):
    """Export PNG multi-line cho stress test."""
    streams = [r['streams']         for r in stress_results]
    mbps    = [r['throughput_mbps'] for r in stress_results]
    delays  = [max(r['avg_delay_ms'],0) for r in stress_results]
    losses  = [r['loss_pct']        for r in stress_results]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.patch.set_facecolor('#1a1a2e')
    fig.suptitle('Stress Test: Multi-Stream iperf3', color='#FF6B35',
                 fontsize=13, fontweight='bold')

    for ax, vals, ylabel, color, unit in [
        (axes[0], mbps,   'Throughput', '#FF6B35', 'Mbps'),
        (axes[1], delays, 'Avg Delay',  '#4a9eff', 'ms'),
        (axes[2], losses, 'Loss',       '#E63946', '%'),
    ]:
        ax.set_facecolor('#16213e')
        ax.plot(streams, vals, 'o-', color=color, linewidth=2, markersize=6)
        ax.set_xlabel('iperf3 Streams', color='white')
        ax.set_ylabel(f'{ylabel} ({unit})', color='white')
        ax.tick_params(colors='white')
        for sp in ['top', 'right']:
            ax.spines[sp].set_visible(False)
        for sp in ['bottom', 'left']:
            ax.spines[sp].set_color('#555')
        ax.set_xticks(streams)

    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, 'stress_test.png')
    plt.savefig(path, dpi=120, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()
    log(f'  Saved stress chart: {path}')


def export_traceroute_chart(trace_results: list):
    """Vẽ bảng text hiển thị đường đi + MPLS labels."""
    fig, ax = plt.subplots(figsize=(14, len(trace_results) * 1.5 + 2))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#1a1a2e')
    ax.axis('off')

    rows = [['Path', 'Hops', 'MPLS Labels']]
    for r in trace_results:
        hops_str   = ' -> '.join(r['hops'][:8]) if r['hops'] else 'N/A'
        labels_str = ', '.join(r['mpls_labels']) if r['mpls_labels'] else 'None detected'
        rows.append([r['label'], hops_str, labels_str])

    table = ax.table(
        cellText=rows[1:], colLabels=rows[0],
        loc='center', cellLoc='left'
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.8)

    # Style
    for (row, col), cell in table.get_celld().items():
        cell.set_facecolor('#16213e' if row > 0 else '#E63946')
        cell.set_text_props(color='white')
        cell.set_edgecolor('#555')

    ax.set_title('Traceroute Path & MPLS Label Summary',
                 color='#FF6B35', fontsize=12, fontweight='bold', pad=20)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, 'traceroute_summary.png')
    plt.savefig(path, dpi=120, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()
    log(f'  Saved traceroute chart: {path}')


# ──────────────────────────────────────────────────────────────────────────────
# 6. EXPORT CSV
# ──────────────────────────────────────────────────────────────────────────────
def export_ping_csv(ping_results: list):
    path = os.path.join(OUTPUT_DIR, 'ping_results.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['label','src','dst','avg_ms','min_ms',
                                          'max_ms','mdev_ms','loss_pct'])
        w.writeheader()
        w.writerows(ping_results)
    log(f'  Saved CSV: {path}')


def export_throughput_csv(tp_results: list):
    path = os.path.join(OUTPUT_DIR, 'throughput_results.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['label','src','dst','throughput_mbps'])
        w.writeheader()
        w.writerows(tp_results)
    log(f'  Saved CSV: {path}')


def export_stress_csv(stress_results: list):
    path = os.path.join(OUTPUT_DIR, 'stress_test.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['streams','throughput_mbps',
                                           'avg_delay_ms','jitter_ms','loss_pct'])
        w.writeheader()
        w.writerows(stress_results)
    log(f'  Saved CSV: {path}')


def export_traceroute_csv(trace_results: list):
    path = os.path.join(OUTPUT_DIR, 'traceroute_results.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['label','src','dst','hops','mpls_labels'])
        for r in trace_results:
            w.writerow([r['label'], r['src'], r['dst'],
                        ' -> '.join(r['hops']),
                        '; '.join(r['mpls_labels'])])
    log(f'  Saved CSV: {path}')


# ──────────────────────────────────────────────────────────────────────────────
# HELPER
# ──────────────────────────────────────────────────────────────────────────────
def _ip_to_node(ip: str) -> str:
    """Tìm node name từ IP."""
    for node, node_ip in NODE_IP.items():
        if node_ip == ip:
            return node
    return None


def _check_netns_available(node: str) -> bool:
    """Kiểm tra namespace của node có tồn tại không."""
    result = subprocess.run(
        f'ip netns list | grep -w {node}',
        shell=True, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def check_prereqs():
    """Kiểm tra các namespace Mininet có đang chạy không."""
    log('Checking Mininet namespaces...')
    missing = []
    for node in ['host1', 'web1', 'admin1', 'PE1']:
        if not _check_netns_available(node):
            missing.append(node)
    if missing:
        log(f'[WARN] Namespaces not found: {missing}')
        log('  Make sure topology.py is running first.')
        return False
    log('  All required namespaces found.')
    return True


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def run_all():
    """Chạy toàn bộ test suite."""
    log('='*60)
    log('Metro Ethernet MPLS – Full Test Suite')
    log('='*60)

    if os.geteuid() != 0:
        log('[WARN] Not running as root; some netns operations may fail.')

    # 0. FRR status (backbone health check đầu tiên)
    run_frr_status_report()

    # 1. Ping / delay / jitter / loss
    ping_res = run_ping_test(count=10)
    export_ping_charts(ping_res)
    export_ping_csv(ping_res)

    # 2. Traceroute
    trace_res = run_all_traceroutes()
    export_traceroute_chart(trace_res)
    export_traceroute_csv(trace_res)

    # 3. Throughput
    tp_res = run_throughput_test()
    export_throughput_chart(tp_res)
    export_throughput_csv(tp_res)

    # 4. Stress test
    stress_res = run_stress_test(src='host1', dst_ip='192.168.31.1')
    export_stress_chart(stress_res)
    export_stress_csv(stress_res)

    log('\n' + '='*60)
    log(f'All results saved to: {OUTPUT_DIR}')
    log('='*60)


def main():
    parser = argparse.ArgumentParser(description='Metro MPLS Test Tool')
    parser.add_argument('--all',        action='store_true', help='Run all tests')
    parser.add_argument('--ping',       action='store_true', help='Ping/delay/loss test')
    parser.add_argument('--traceroute', action='store_true', help='Traceroute test')
    parser.add_argument('--throughput', action='store_true', help='Throughput test')
    parser.add_argument('--stress',     action='store_true', help='Stress test')
    parser.add_argument('--no-check',   action='store_true',
                        help='Skip namespace prereq check')
    args = parser.parse_args()

    if not args.no_check:
        if not check_prereqs():
            sys.exit(1)

    if args.all or (not any([args.ping, args.traceroute, args.throughput, args.stress])):
        run_all()
        return

    if args.ping:
        ping_res = run_ping_test()
        export_ping_charts(ping_res)
        export_ping_csv(ping_res)

    if args.traceroute:
        trace_res = run_all_traceroutes()
        export_traceroute_chart(trace_res)
        export_traceroute_csv(trace_res)

    if args.throughput:
        tp_res = run_throughput_test()
        export_throughput_chart(tp_res)
        export_throughput_csv(tp_res)

    if args.stress:
        stress_res = run_stress_test()
        export_stress_chart(stress_res)
        export_stress_csv(stress_res)

    log(f'Done. Outputs: {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
