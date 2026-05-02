# Metro Ethernet MPLS trên Mininet

## 1. Mô tả ngắn
Mô hình gồm:
- CN1: flat network
- CN2: 3-layer core/distribution/access
- CN3: leaf-spine 2-layer có OSPF nội bộ + ECMP
- ISP backbone: PE1/PE2/PE3 + P1/P2/P3/P4
- Underlay backbone: OSPF + LDP/MPLS
- Metro Ethernet service: VPLS PE1 <-> PE3 cho lưu lượng CN1 <-> CN3
- Routed service: static CE-PE / PE remote routes cho CN2 và các kiểm thử routed MPLS

## 2. Phụ thuộc
Cần cài:
- Python3
- Mininet
- FRRouting (zebra, ospfd, ldpd, staticd, vtysh)
- traceroute
- iproute2
- iperf3 hoặc iperf
- matplotlib

Ví dụ Ubuntu:
```bash
sudo apt update
sudo apt install -y mininet frr frr-pythontools traceroute iperf3 python3-matplotlib