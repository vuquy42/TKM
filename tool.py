#!/usr/bin/env python3
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from addressing import TRACE_NODES

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_DIR / "measurements.log"


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ns_cmd(node: str, cmd: str, timeout: int = 90) -> str:
    full = f"ip netns exec {shlex.quote(node)} bash -lc {shlex.quote(cmd)}"
    res = subprocess.run(
        full,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return res.stdout


def which_iperf():
    out = subprocess.run(
        "command -v iperf3 || command -v iperf || true",
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout.strip()
    return out.splitlines()[0] if out else None


IPERF_BIN = which_iperf()


def prime_arp(src: str, dst_ip: str):
    ns_cmd(src, f"ping -c 1 -W 1 {dst_ip} >/dev/null 2>&1 || true", timeout=10)


def parse_ping_summary(out: str):
    loss = 100.0
    avg = 0.0
    jitter = 0.0

    m1 = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", out)
    if m1:
        loss = float(m1.group(1))

    m2 = re.search(r"min/avg/max(?:/mdev)? = [\d\.]+/([\d\.]+)/[\d\.]+(?:/([\d\.]+))?", out)
    if m2:
        avg = float(m2.group(1))
        if m2.group(2):
            jitter = float(m2.group(2))
    else:
        m3 = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/[\d\.]+/([\d\.]+)", out)
        if m3:
            avg = float(m3.group(1))
            jitter = float(m3.group(2))

    return {"avg_ms": avg, "jitter_ms": jitter, "loss_pct": loss, "raw": out}


def ping_metrics(src: str, dst: str, count: int = 10):
    dst_ip = TRACE_NODES[dst]
    prime_arp(src, dst_ip)
    out = ns_cmd(src, f"ping -c {count} -i 0.2 -W 1 -q {dst_ip}")
    data = parse_ping_summary(out)
    data["src"] = src
    data["dst"] = dst
    return data


def traceroute_metrics(src: str, dst: str):
    dst_ip = TRACE_NODES[dst]
    out = ns_cmd(src, f"traceroute -n -q 1 -w 1 -m 12 {dst_ip}", timeout=60)
    hops = []
    labels = []

    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+([0-9\.\*]+)", line)
        if m and m.group(2) != "*":
            hops.append(m.group(2))
        if "MPLS" in line or "Label=" in line or "Labels" in line:
            labels.append(line.strip())

    return {
        "src": src,
        "dst": dst,
        "hop_count": len(hops),
        "path": " -> ".join(hops),
        "mpls_seen": "yes" if labels else "no",
        "mpls_lines": " | ".join(labels),
        "raw": out,
    }


def _iperf_start_server(dst: str):
    if IPERF_BIN is None:
        raise RuntimeError("iperf/iperf3 not found")

    if IPERF_BIN.endswith("iperf3"):
        cmd = f"{IPERF_BIN} -s -1 >/tmp/iperf_server_{dst}.log 2>&1"
        return subprocess.Popen(
            f"ip netns exec {dst} bash -lc {shlex.quote(cmd)}",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    else:
        ns_cmd(dst, "pkill -9 iperf >/dev/null 2>&1 || true", timeout=10)
        ns_cmd(dst, f"{IPERF_BIN} -s -D", timeout=10)
        return None


def _iperf_stop_server(dst: str):
    if IPERF_BIN and IPERF_BIN.endswith("iperf3"):
        ns_cmd(dst, "pkill -9 iperf3 >/dev/null 2>&1 || true", timeout=10)
    else:
        ns_cmd(dst, "pkill -9 iperf >/dev/null 2>&1 || true", timeout=10)


def parse_iperf_mbps(out: str):
    patterns = [
        r"([\d\.]+)\s+Mbits/sec",
        r"receiver\s+.*?([\d\.]+)\s+Mbits/sec",
        r"sender\s+.*?([\d\.]+)\s+Mbits/sec",
    ]
    for p in patterns:
        m = re.search(p, out, re.S)
        if m:
            return float(m.group(1))
    return 0.0


def throughput_metrics(src: str, dst: str, duration: int = 8, parallel: int = 1):
    if IPERF_BIN is None:
        return {"src": src, "dst": dst, "parallel": parallel, "mbps": 0.0, "raw": "iperf not found"}

    dst_ip = TRACE_NODES[dst]
    prime_arp(src, dst_ip)
    _iperf_stop_server(dst)
    server = _iperf_start_server(dst)
    time.sleep(1)

    if IPERF_BIN.endswith("iperf3"):
        cmd = f"{IPERF_BIN} -c {dst_ip} -t {duration} -P {parallel}"
    else:
        cmd = f"{IPERF_BIN} -c {dst_ip} -t {duration} -P {parallel} -f m"

    out = ns_cmd(src, cmd, timeout=duration + 30)
    mbps = parse_iperf_mbps(out)

    if server is not None:
        try:
            server.wait(timeout=duration + 10)
        except Exception:
            pass
    _iperf_stop_server(dst)

    return {"src": src, "dst": dst, "parallel": parallel, "mbps": mbps, "raw": out}


def stress_metrics(flow_src="host1", flow_dst="web1", probe_src="host2", probe_dst="web1"):
    results = []
    for p in [1, 4, 8]:
        log(f"Stress run parallel={p}")
        if IPERF_BIN is None:
            results.append({"parallel": p, "mbps": 0.0, "avg_ms": 0.0, "jitter_ms": 0.0, "loss_pct": 100.0})
            continue

        dst_ip = TRACE_NODES[flow_dst]
        _iperf_stop_server(flow_dst)
        _iperf_start_server(flow_dst)
        time.sleep(1)

        if IPERF_BIN.endswith("iperf3"):
            iperf_cmd = f"{IPERF_BIN} -c {dst_ip} -t 12 -P {p}"
        else:
            iperf_cmd = f"{IPERF_BIN} -c {dst_ip} -t 12 -P {p} -f m"

        proc = subprocess.Popen(
            f"ip netns exec {flow_src} bash -lc {shlex.quote(iperf_cmd)}",
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        time.sleep(2)
        ping = ping_metrics(probe_src, probe_dst, count=12)
        out, _ = proc.communicate(timeout=40)
        _iperf_stop_server(flow_dst)

        results.append(
            {
                "parallel": p,
                "mbps": parse_iperf_mbps(out),
                "avg_ms": ping["avg_ms"],
                "jitter_ms": ping["jitter_ms"],
                "loss_pct": ping["loss_pct"],
                "raw": out,
            }
        )
    return results


def write_csv(path: Path, rows, headers):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in headers})


def bar_png(path: Path, title: str, labels, values, ylabel: str):
    plt.figure(figsize=(10, 5))
    plt.bar(labels, values)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=140)
    plt.close()


def traceroute_png(path: Path, rows):
    fig, ax = plt.subplots(figsize=(12, max(3, len(rows) * 0.8)))
    ax.axis("off")
    table_data = [["pair", "hop_count", "mpls_seen", "path"]]
    for r in rows:
        table_data.append([f"{r['src']}->{r['dst']}", r["hop_count"], r["mpls_seen"], r["path"][:80]])
    tbl = ax.table(cellText=table_data, loc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.5)
    plt.title("Traceroute / Path Summary")
    plt.tight_layout()
    plt.savefig(path, dpi=140)
    plt.close()


def main():
    if os.geteuid() != 0:
        print("Run as root: sudo python3 source/tool.py")
        sys.exit(1)

    log("Starting measurement suite")

    ping_pairs = [("host1", "admin1"), ("host1", "web1"), ("admin1", "db1")]
    throughput_pairs = [("host1", "web1"), ("admin1", "db1"), ("lab1", "dns1")]
    trace_pairs = [("host1", "admin1"), ("admin1", "db1"), ("host1", "web1")]

    ping_rows = []
    for s, d in ping_pairs:
        log(f"Ping metrics {s} -> {d}")
        ping_rows.append(ping_metrics(s, d, count=10))

    thr_rows = []
    for s, d in throughput_pairs:
        log(f"Throughput metrics {s} -> {d}")
        thr_rows.append(throughput_metrics(s, d, duration=8, parallel=1))

    trace_rows = []
    for s, d in trace_pairs:
        log(f"Traceroute metrics {s} -> {d}")
        trace_rows.append(traceroute_metrics(s, d))
        if trace_rows[-1]["mpls_seen"] == "yes":
            log(f"MPLS label hint detected: {trace_rows[-1]['mpls_lines']}")

    stress_rows = stress_metrics()

    # CSV
    write_csv(
        OUTPUT_DIR / "delay_comparison.csv",
        ping_rows,
        ["src", "dst", "avg_ms", "jitter_ms", "loss_pct"],
    )
    write_csv(
        OUTPUT_DIR / "packet_loss_comparison.csv",
        ping_rows,
        ["src", "dst", "loss_pct"],
    )
    write_csv(
        OUTPUT_DIR / "throughput_comparison.csv",
        thr_rows,
        ["src", "dst", "parallel", "mbps"],
    )
    write_csv(
        OUTPUT_DIR / "stress_test_comparison.csv",
        stress_rows,
        ["parallel", "mbps", "avg_ms", "jitter_ms", "loss_pct"],
    )
    write_csv(
        OUTPUT_DIR / "traceroute_summary.csv",
        trace_rows,
        ["src", "dst", "hop_count", "mpls_seen", "path", "mpls_lines"],
    )

    # PNG
    bar_png(
        OUTPUT_DIR / "throughput_comparison.png",
        "Throughput Comparison",
        [f"{r['src']}->{r['dst']}" for r in thr_rows],
        [r["mbps"] for r in thr_rows],
        "Mbps",
    )
    bar_png(
        OUTPUT_DIR / "delay_comparison.png",
        "Delay Comparison",
        [f"{r['src']}->{r['dst']}" for r in ping_rows],
        [r["avg_ms"] for r in ping_rows],
        "ms",
    )
    bar_png(
        OUTPUT_DIR / "packet_loss_comparison.png",
        "Packet Loss Comparison",
        [f"{r['src']}->{r['dst']}" for r in ping_rows],
        [r["loss_pct"] for r in ping_rows],
        "%",
    )
    bar_png(
        OUTPUT_DIR / "stress_test_comparison.png",
        "Stress Test Comparison",
        [str(r["parallel"]) for r in stress_rows],
        [r["mbps"] for r in stress_rows],
        "Mbps",
    )
    traceroute_png(OUTPUT_DIR / "path_traceroute_summary.png", trace_rows)

    log(f"Done. Outputs stored in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()