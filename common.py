#!/usr/bin/env python3
import os
import shutil
import subprocess
import time
from pathlib import Path

from mininet.log import info
from mininet.node import Node

BASE_DIR = Path("/tmp/metro_mpls")
DEFAULT_MTU = 1600
FRR_DIR_CANDIDATES = ["/usr/lib/frr", "/usr/libexec/frr", "/usr/sbin", "/usr/bin"]


def daemon_bin(name: str) -> str:
    for d in FRR_DIR_CANDIDATES:
        p = Path(d) / name
        if p.exists():
            return str(p)
    return name


def host_cmd(cmd: str) -> str:
    return subprocess.run(
        cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ).stdout


def cleanup_all():
    info("*** Cleanup Mininet/FRR state\n")
    host_cmd("mn -c >/dev/null 2>&1 || true")
    host_cmd("pkill -9 zebra >/dev/null 2>&1 || true")
    host_cmd("pkill -9 ospfd >/dev/null 2>&1 || true")
    host_cmd("pkill -9 ldpd >/dev/null 2>&1 || true")
    host_cmd("pkill -9 staticd >/dev/null 2>&1 || true")
    host_cmd("pkill -9 watchfrr >/dev/null 2>&1 || true")
    if BASE_DIR.exists():
        shutil.rmtree(BASE_DIR, ignore_errors=True)
    BASE_DIR.mkdir(parents=True, exist_ok=True)


def load_mpls_kernel():
    info("*** Loading MPLS kernel modules\n")
    host_cmd("modprobe mpls_router || true")
    host_cmd("modprobe mpls_iptunnel || true")
    host_cmd("sysctl -w net.mpls.platform_labels=100000 >/dev/null")
    host_cmd("sysctl -w net.mpls.conf.lo.input=1 >/dev/null")


def wait_msg(seconds: int, msg: str):
    info(f"*** {msg} ({seconds}s)\n")
    time.sleep(seconds)


def run_dir(node_name: str) -> Path:
    p = BASE_DIR / node_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class LinuxMPLSRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1 >/dev/null")
        self.cmd("sysctl -w net.ipv4.fib_multipath_hash_policy=1 >/dev/null")
        self.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null")
        self.cmd("sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null")
        self.cmd("sysctl -w net.mpls.platform_labels=100000 >/dev/null")
        self.cmd("sysctl -w net.mpls.conf.lo.input=1 >/dev/null")
        self.cmd("modprobe mpls_router 2>/dev/null || true")
        self.cmd("modprobe mpls_iptunnel 2>/dev/null || true")

    def terminate(self):
        rd = run_dir(self.name)
        for pidf in ["zebra.pid", "ospfd.pid", "ldpd.pid", "staticd.pid"]:
            p = rd / pidf
            if p.exists():
                try:
                    self.cmd(f"kill -9 $(cat {p}) >/dev/null 2>&1 || true")
                except Exception:
                    pass
        super().terminate()


class RouterCE(LinuxMPLSRouter):
    pass


class RouterPE(LinuxMPLSRouter):
    pass


class RouterP(LinuxMPLSRouter):
    pass


def ensure_netns_links(net):
    os.makedirs("/var/run/netns", exist_ok=True)
    for name, node in net.nameToNode.items():
        if hasattr(node, "pid") and node.pid:
            host_cmd(f"ln -sf /proc/{node.pid}/ns/net /var/run/netns/{name}")


def set_link_up(node, intf: str, mtu: int = DEFAULT_MTU):
    node.cmd(f"ip link set dev {intf} up")
    node.cmd(f"ip link set dev {intf} mtu {mtu}")


def add_ip(node, intf: str, cidr: str):
    node.cmd(f"ip addr flush dev {intf}")
    node.cmd(f"ip addr add {cidr} dev {intf}")
    node.cmd(f"ip link set dev {intf} up")


def add_loopback(node, cidr: str):
    node.cmd("ip addr flush dev lo")
    node.cmd("ip addr add 127.0.0.1/8 dev lo")
    node.cmd(f"ip addr add {cidr} dev lo")
    node.cmd("ip link set dev lo up")
    node.cmd("sysctl -w net.mpls.conf.lo.input=1 >/dev/null")


def add_vlan_subif(node, parent: str, vlan: int, ip_cidr: str = None, mtu: int = DEFAULT_MTU) -> str:
    subif = f"{parent}.{vlan}"
    node.cmd(f"ip link add link {parent} name {subif} type vlan id {vlan} 2>/dev/null || true")
    node.cmd(f"ip link set dev {subif} mtu {mtu}")
    node.cmd(f"ip link set dev {subif} up")
    if ip_cidr:
        node.cmd(f"ip addr flush dev {subif}")
        node.cmd(f"ip addr add {ip_cidr} dev {subif}")
    return subif


def add_bridge(node, br: str, members=None, ip_cidr: str = None, mtu: int = DEFAULT_MTU):
    members = members or []
    node.cmd(f"ip link add name {br} type bridge 2>/dev/null || true")
    node.cmd(f"ip link set dev {br} mtu {mtu}")
    node.cmd(f"ip link set dev {br} up")
    for m in members:
        node.cmd(f"ip addr flush dev {m}")
        node.cmd(f"ip link set dev {m} up")
        node.cmd(f"ip link set dev {m} master {br}")
    if ip_cidr:
        node.cmd(f"ip addr flush dev {br}")
        node.cmd(f"ip addr add {ip_cidr} dev {br}")


def add_route(node, prefix: str, via: str = None, dev: str = None):
    if via and dev:
        node.cmd(f"ip route replace {prefix} via {via} dev {dev}")
    elif via:
        node.cmd(f"ip route replace {prefix} via {via}")
    elif dev:
        node.cmd(f"ip route replace {prefix} dev {dev}")


def enable_mpls_on_interfaces(node, interfaces):
    for intf in interfaces:
        node.cmd(f"sysctl -w net.mpls.conf.{intf}.input=1 >/dev/null")


def start_frr(node, daemons=("zebra", "ospfd", "ldpd")):
    rd = run_dir(node.name)
    base = f"hostname {node.name}\nlog file {rd}/frr.log\nservice integrated-vtysh-config\n!\n"
    for d in daemons:
        write_file(rd / f"{d}.conf", base)

    zebra = daemon_bin("zebra")
    ospfd = daemon_bin("ospfd")
    ldpd = daemon_bin("ldpd")
    staticd = daemon_bin("staticd")

    node.cmd(f"{zebra} -d -f {rd/'zebra.conf'} -z {rd/'zserv.api'} -i {rd/'zebra.pid'} -A 127.0.0.1")
    time.sleep(0.5)

    if "ospfd" in daemons:
        node.cmd(f"{ospfd} -d -f {rd/'ospfd.conf'} -z {rd/'zserv.api'} -i {rd/'ospfd.pid'} -A 127.0.0.1")
        time.sleep(0.3)

    if "ldpd" in daemons:
        node.cmd(f"{ldpd} -d -f {rd/'ldpd.conf'} -z {rd/'zserv.api'} -i {rd/'ldpd.pid'} -A 127.0.0.1")
        time.sleep(0.3)

    if "staticd" in daemons:
        node.cmd(f"{staticd} -d -f {rd/'staticd.conf'} -z {rd/'zserv.api'} -i {rd/'staticd.pid'} -A 127.0.0.1")
        time.sleep(0.3)


def vtysh_apply(node, lines):
    rd = run_dir(node.name)
    cfg = "configure terminal\n" + "\n".join(lines) + "\nend\n"
    cfg_path = rd / "apply.cli"
    write_file(cfg_path, cfg)
    return node.cmd(f"vtysh -f {cfg_path}")


def interface_exists(node, intf: str) -> bool:
    out = node.cmd(f"ip link show {intf} 2>/dev/null")
    return intf in out


def ovs_access(sw, port_name: str, vlan: int):
    sw.cmd(f"ovs-vsctl --if-exists clear port {port_name} tag trunks vlan_mode")
    sw.cmd(f"ovs-vsctl set port {port_name} tag={vlan}")


def ovs_trunk(sw, port_name: str, vlans):
    vl = ",".join(str(v) for v in vlans)
    sw.cmd(f"ovs-vsctl --if-exists clear port {port_name} tag trunks vlan_mode")
    sw.cmd(f"ovs-vsctl set port {port_name} vlan_mode=trunk trunks={vl}")