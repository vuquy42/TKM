# Metro Ethernet MPLS – Mininet Simulation

**Đề tài:** Thiết kế và triển khai mạng Metro Ethernet sử dụng MPLS cho kết nối đa chi nhánh doanh nghiệp.  
**Sinh viên:** Huỳnh Văn Dũng – MSSV: 52300190  
**GVHD:** Lê Viết Thanh  
**Trường:** Đại học Tôn Đức Thắng – Khoa CNTT

---

## 1. Kiến trúc tổng quan

```
          ┌─────────── ISP MPLS Backbone ───────────────┐
          │  P1 ─── P2                                   │
          │  │  ╲  ╱  │                                  │
          │  │   ╲╱   │                                  │
          │  │   ╱╲   │                                  │
          │  │  ╱  ╲  │                                  │
          │  P3 ─── P4                                   │
          │  │         │         │                       │
          │ PE1       PE2       PE3                       │
          └─ │ ─────── │ ─────── │ ──────────────────────┘
             │         │         │
            CE1       CE2       CE3
             │         │         │
    ┌────────┤  ┌──────┤  ┌──────┤
    │ CN1    │  │ CN2  │  │ CN3  │
    │ Flat   │  │ 3-L  │  │ L-S  │
    └────────┘  └──────┘  └──────┘
```

| Layer    | Nodes             | Vai trò                               |
|----------|-------------------|---------------------------------------|
| P        | P1, P2, P3, P4    | Label switching trung tâm             |
| PE       | PE1, PE2, PE3     | Biên ISP, kết nối CE, VPLS endpoint  |
| CE       | CE1, CE2, CE3     | Router biên khách hàng                |
| CN1      | sw_cn1, host1-4   | Mạng phẳng 192.168.10.0/24           |
| CN2      | core_sw, dist1/2, acc1-3 | 3-lớp VLAN 21/22/23         |
| CN3      | spine1/2, leaf1-3, web/dns/db | Leaf-Spine ECMP         |

---

## 2. Yêu cầu môi trường

```bash
# Ubuntu 20.04 / 22.04
sudo apt-get update
sudo apt-get install -y \
    mininet \
    python3-mininet \
    frr \
    iperf3 \
    traceroute \
    iproute2 \
    python3-matplotlib \
    python3-numpy

# Kiểm tra FRR
sudo systemctl enable frr
vtysh --version

# Bật MPLS trong kernel (nếu chưa có trong module)
grep -r mpls /proc/modules || sudo modprobe mpls_router mpls_iptunnel
```

---

## 3. Cấu trúc thư mục

```
source/
├── topology.py          # Orchestrator chính – chạy file này
├── common.py            # LinuxRouter class, FRR helpers, sysctl
├── addressing.py        # Quy hoạch IP toàn bộ dự án
├── backbone.py          # P/PE nodes, OSPF, LDP, GRE pseudowire
├── cn1_flat.py          # Chi nhánh 1 – Flat Network
├── cn2_3layer.py        # Chi nhánh 2 – Core-Distribution-Access
├── cn3_leafspine.py     # Chi nhánh 3 – Leaf-Spine + ECMP
├── tool.py              # Đo đạc, export PNG/CSV
├── README.md            # File này
└── outputs/             # Kết quả test (tự tạo khi chạy)
    ├── delay_comparison.png
    ├── jitter_comparison.png
    ├── loss_comparison.png
    ├── throughput_comparison.png
    ├── stress_test.png
    ├── traceroute_summary.png
    ├── ping_results.csv
    ├── throughput_results.csv
    ├── stress_test.csv
    ├── traceroute_results.csv
    ├── frr_status.csv
    └── test_report.log
```

---

## 4. Trình tự khởi chạy

### Bước 1 – Xem địa chỉ IP plan
```bash
cd source/
python3 addressing.py
```

### Bước 2 – Khởi động topology
```bash
sudo python3 topology.py
```
Sau khi chạy, Mininet CLI sẽ mở ra (khoảng 60–70 giây do chờ OSPF/LDP hội tụ).

### Bước 3 – Chạy test (trong terminal khác)
```bash
sudo python3 tool.py --all
```

---

## 5. Trình tự khởi tạo bên trong (topology.py)

| Bước | Hàm                        | Mô tả                             |
|------|----------------------------|-----------------------------------|
| 1    | `cleanup_mininet()`        | mn -c, kill FRR processes        |
| 2    | `load_mpls_modules()`      | modprobe mpls_router/iptunnel    |
| 3    | `addHost/addSwitch/addLink`| Dựng topo đầy đủ                 |
| 4    | `net.start()`              | Khởi động Mininet                |
| 5    | `configure_backbone()`     | Gán IP P/PE, loopback, MTU       |
|      | `configure_cn1/2/3()`      | Gán IP CE, branch hosts          |
|      | `enable_mpls_pe_ce()`      | Bật MPLS trên PE→CE interfaces   |
| 6    | `start_backbone_frr()`     | Khởi động zebra+ospfd+ldpd       |
|      | `start_cn3_frr()`          | OSPF nội bộ CN3 (optional)       |
| 7    | `configure_ospf()`         | OSPF area 0 trên P/PE            |
| 8    | `configure_ldp()`          | LDP trên P-P và P-PE links       |
| 9    | `configure_vpls_gre()`     | GRE pseudowire PE1↔PE3           |
| 10   | `wait_convergence()`       | 45s OSPF + 15s LDP               |

---

## 6. Lệnh kiểm tra (Checklist)

### Trong Mininet CLI:

```
# Ping intra-branch
mininet> host1 ping -c 3 192.168.10.2          # CN1 intra
mininet> admin1 ping -c 3 192.168.22.1          # CN2 inter-VLAN
mininet> web1 ping -c 3 192.168.33.1            # CN3 cross-leaf

# Ping cross-branch (qua MPLS backbone)
mininet> host1 ping -c 3 192.168.31.1           # CN1 -> CN3
mininet> host1 ping -c 3 192.168.21.1           # CN1 -> CN2

# Traceroute qua backbone
mininet> host1 traceroute 192.168.31.1

# OSPF neighbors
mininet> PE1 vtysh -c "show ip ospf neighbor"
mininet> P1 vtysh -c "show ip ospf neighbor"

# LDP neighbors
mininet> PE1 vtysh -c "show mpls ldp neighbor"

# MPLS routing table
mininet> PE1 vtysh -c "show ip route"
mininet> P1 ip route show

# ECMP routes trên CN3
mininet> leaf1 ip route
mininet> spine1 ip route

# GRE pseudowire
mininet> PE1 ip tunnel show
mininet> PE1 ip link show br-vpls
```

### Dấu hiệu thành công:

| Kiểm tra | Thành công | Thất bại |
|----------|-----------|---------|
| OSPF neighbors | `State: Full` | không có neighbor |
| LDP neighbors | `OPERATIONAL` | `INITIALIZED` hoặc trống |
| Traceroute MPLS | `[MPLS: Label xxx]` | chỉ thấy IP |
| Ping intra | 0% loss | 100% loss |
| Ping cross-branch | ≤ 2ms | timeout |
| GRE tunnel | `gre-pw0: UNKNOWN` | không có interface |

---

## 7. Chạy test và xuất kết quả

```bash
# Chạy tất cả (PNG + CSV)
sudo python3 tool.py --all

# Chạy từng phần
sudo python3 tool.py --ping           # delay/jitter/loss
sudo python3 tool.py --traceroute     # path + MPLS labels
sudo python3 tool.py --throughput     # iperf3 bandwidth
sudo python3 tool.py --stress         # multi-stream stress

# Kết quả lưu trong outputs/
ls -la outputs/
```

---

## 8. Ghi chú kỹ thuật quan trọng

### VPLS Fallback
FRRouting cài từ `apt` không hỗ trợ đầy đủ lệnh VPLS (`pseudowire-class`, `xconnect`). Dự án dùng **GRE tunnel over MPLS** làm giải pháp thay thế:
- GRE endpoint = loopback PE (reachable qua MPLS LSP)
- Frame L2 từ CN1 host được đóng gói GRE → đi qua MPLS → CE3 → CN3

### ECMP trên Leaf-Spine
Kernel Linux hỗ trợ multipath routing:
```bash
# Kiểm tra ECMP đang hoạt động
ip route show | grep -E 'nexthop|via.*weight'
sysctl net.ipv4.fib_multipath_hash_policy  # phải = 1
```

### MTU
Tất cả interface backbone được set MTU = 1512 để tránh fragmentation khi thêm MPLS label (4 bytes/label).

### Thời gian chờ
- OSPF hội tụ: ~30–45 giây
- LDP LSP: ~15 giây sau OSPF
- Ping đầu tiên có thể fail do ARP – bình thường

---

## 9. Troubleshooting

```bash
# Xem log FRR
cat /tmp/PE1/ospfd.log
cat /tmp/P1/ldpd.log

# Kiểm tra MPLS kernel
cat /proc/sys/net/mpls/platform_labels      # phải > 0
cat /proc/sys/net/mpls/conf/PE1-eth0/input  # phải = 1

# Restart FRR trên 1 node (từ Mininet CLI)
mininet> PE1 /usr/lib/frr/ospfd -d -A 127.0.0.1 -f /tmp/PE1/ospfd.conf -i /tmp/PE1/ospfd.pid

# Cleanup hoàn toàn
sudo python3 topology.py --clean
sudo mn -c
```
