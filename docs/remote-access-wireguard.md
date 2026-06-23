# jt-ipam 遠端連線安全架構（WireGuard VPN）— 技術部署文件

**版本**：1.0　　**日期**：2026-06-23　　**適用條件**：所有客戶內部網段**不重疊**

> **與 SOCKS5+frp 方案的差異**：本方案採用 Linux Kernel 層的 WireGuard，
> 架構更簡單、攻擊面更小，且 jt-ipam **無需修改任何程式碼**。
> 適用前提：70 家客戶的內部網段各自唯一，無 192.168.x.x 等 RFC1918 重疊。

---

## 一、整體架構說明

### 架構圖

```
使用者瀏覽器
  │ WebSocket（HTTPS）
  ▼
jt-ipam Backend（IPAM Server）
  │ asyncssh.connect("192.168.1.10")   ← 程式碼完全不變
  │
  │ OS 路由表自動判斷：
  │   192.168.1.0/24 → via wg0（Customer A 的 WireGuard peer）
  │   10.10.0.0/24   → via wg0（Customer B 的 WireGuard peer）
  ▼
WireGuard wg0（Kernel 層加密隧道）
  ├── Peer: Customer A ────────────── Customer A 內部網路（192.168.1.0/24）
  ├── Peer: Customer B ────────────── Customer B 內部網路（10.10.0.0/24）
  └── Peer: Customer C ...
```

### 關鍵設計原則

- **Kernel 層加密**：WireGuard 實作於 Linux Kernel，程式碼約 4000 行，已通過多次獨立安全稽核
- **零程式碼修改**：OS 路由表自動將封包送到正確的 WireGuard peer，jt-ipam 無感知
- **客戶端主動連出**：WireGuard 支援 NAT 穿透（PersistentKeepalive），客戶無需開放任何入站 port
- **每客戶獨立 Peer**：每家客戶有獨立的公私鑰對，洩漏時可單獨移除 peer，不影響其他客戶

### IP 位址規劃

| 網段 | 用途 |
|------|------|
| `10.200.0.0/24` | WireGuard 隧道管理網段（IPAM Server 與各客戶 peer 的隧道 IP）|
| `10.200.0.1` | IPAM Server 的隧道 IP |
| `10.200.0.2` | 客戶 A 的隧道 IP |
| `10.200.0.3` | 客戶 B 的隧道 IP |
| `10.200.0.x` | 依此類推，最多支援 253 家客戶 |
| 客戶實際內網 | 由客戶各自管理，不得與其他客戶重疊 |

---

## 二、元件清單

| 元件 | 用途 | 部署位置 |
|------|------|---------|
| WireGuard（Kernel 模組）| VPN 隧道加密 | IPAM Server + 各客戶主機 |
| wg-quick | WireGuard 設定管理工具 | IPAM Server + 各客戶主機 |
| ufw | 防火牆 | IPAM Server |
| fail2ban | 暴力破解防護 | IPAM Server |
| auditd | 系統操作稽核 | IPAM Server |
| Google Authenticator | SSH 雙因素驗證 | IPAM Server |

---

## 三、環境需求

### IPAM Server

- OS：Ubuntu 22.04 LTS（Kernel 5.6+ 內建 WireGuard）
- 公開固定 IP
- 開放 port：`51820/udp`（WireGuard，限客戶來源 IP）

### 客戶端主機

- OS：Ubuntu 20.04+（Kernel 5.6+ 建議）或 Debian 11+
- 需要連到客戶內部網路（即客戶內網的 gateway 或任意可路由的主機）
- 只需**出站** UDP 到 IPAM Server port 51820
- 無需任何入站 port 開放

---

## 四、金鑰管理

WireGuard 使用公私鑰對（Curve25519），每家客戶各自持有一對，私鑰永不離開該主機。

### 4.1 IPAM Server 金鑰生成（執行一次）

```bash
sudo apt install wireguard -y

# 生成 Server 金鑰對
wg genkey | sudo tee /etc/wireguard/server.key | wg pubkey | sudo tee /etc/wireguard/server.pub
sudo chmod 600 /etc/wireguard/server.key

echo "Server 公鑰（需填入客戶 frpc 設定）："
sudo cat /etc/wireguard/server.pub
```

### 4.2 客戶端金鑰生成（在客戶主機上執行）

```bash
sudo apt install wireguard -y

# 生成客戶金鑰對
wg genkey | sudo tee /etc/wireguard/client.key | wg pubkey | sudo tee /etc/wireguard/client.pub
sudo chmod 600 /etc/wireguard/client.key

echo "客戶公鑰（需填入 IPAM Server peer 設定）："
sudo cat /etc/wireguard/client.pub
```

> **金鑰管理規範**：
> - 私鑰永遠不得離開所在主機，不可透過 email 或聊天軟體傳送
> - 公鑰可以公開交換（交換公鑰即完成雙向信任建立）
> - 移除客戶時，在 Server 設定刪除該客戶的 `[Peer]` 區塊即可，無需其他操作

---

## 五、IPAM Server 部署

### 5.1 WireGuard Server 設定

建立 `/etc/wireguard/wg0.conf`：

```ini
[Interface]
# IPAM Server 的隧道 IP
Address    = 10.200.0.1/24
ListenPort = 51820
PrivateKey = <填入 /etc/wireguard/server.key 的內容>

# 允許封包轉發（讓 IPAM Server 能透過 wg0 路由到客戶內網）
PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT

# ============================================================
# 客戶 A
# ============================================================
[Peer]
# 客戶A的公鑰（從客戶主機取得）
PublicKey  = <客戶A的 client.pub 內容>
# 允許的來源 IP：隧道 IP + 客戶內網網段
AllowedIPs = 10.200.0.2/32, 192.168.1.0/24

# ============================================================
# 客戶 B
# ============================================================
[Peer]
PublicKey  = <客戶B的 client.pub 內容>
AllowedIPs = 10.200.0.3/32, 10.10.0.0/24

# ============================================================
# 後續新增客戶在此繼續加 [Peer] 區塊
# ============================================================
```

啟動並設為開機自動啟動：

```bash
# 啟用 IP 轉發（持久化）
echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-wireguard.conf
sudo sysctl -p /etc/sysctl.d/99-wireguard.conf

sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable wg-quick@wg0 --now

# 確認狀態
sudo wg show wg0
```

---

### 5.2 防火牆設定（ufw）

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH：只開管理 IP
sudo ufw allow from <你公司固定IP> to any port 22 proto tcp

# jt-ipam Web 介面
sudo ufw allow from <你公司固定IP> to any port 443 proto tcp

# WireGuard：每新增一家客戶加一條
sudo ufw allow from <客戶A出口IP> to any port 51820 proto udp
sudo ufw allow from <客戶B出口IP> to any port 51820 proto udp
# ... 依此類推

sudo ufw enable
sudo ufw status numbered
```

---

### 5.3 主機安全加固

> 以下設定與 SOCKS5+frp 方案相同，已部署過可跳過。

#### SSH 強化

```bash
sudo nano /etc/ssh/sshd_config
```

```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers <你的管理帳號>
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
```

```bash
sudo systemctl restart ssh
```

#### fail2ban

```bash
sudo apt install fail2ban -y
```

`/etc/fail2ban/jail.local`：

```ini
[sshd]
enabled  = true
maxretry = 3
bantime  = 3600
findtime = 600
```

```bash
sudo systemctl enable fail2ban --now
```

#### 自動安全更新

```bash
sudo apt install unattended-upgrades -y
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

`/etc/apt/apt.conf.d/50unattended-upgrades`：

```
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Mail "<你的信箱>";
```

#### SSH MFA（TOTP）

```bash
sudo apt install libpam-google-authenticator -y
google-authenticator   # 用管理帳號執行，掃描 QR code
```

`/etc/pam.d/sshd` 頂端加入：

```
auth required pam_google_authenticator.so
```

`/etc/ssh/sshd_config` 修改：

```
ChallengeResponseAuthentication yes
AuthenticationMethods publickey,keyboard-interactive
```

```bash
sudo systemctl restart ssh
```

#### auditd 稽核

```bash
sudo apt install auditd -y

sudo auditctl -w /opt/jt-ipam            -p wa -k jt-ipam-changes
sudo auditctl -w /etc/wireguard/wg0.conf -p wa -k wireguard-config
sudo auditctl -w /etc/ssh/sshd_config    -p wa -k ssh-config
sudo auditctl -w /etc/sudoers            -p wa -k sudoers-changes

sudo systemctl enable auditd --now
```

#### SSH 登入即時告警

建立 `/etc/ssh/login-notify.sh`：

```bash
#!/bin/bash
WEBHOOK="https://your-teams-or-slack-webhook-url"
curl -s -X POST "$WEBHOOK" \
  -H 'Content-type: application/json' \
  -d "{\"text\": \"⚠️ [jt-ipam] SSH 登入：$PAM_USER 從 $PAM_RHOST 於 $(date '+%Y-%m-%d %H:%M:%S')\"}"
```

```bash
chmod 700 /etc/ssh/login-notify.sh
```

`/etc/pam.d/sshd` 加入：

```
session optional pam_exec.so /etc/ssh/login-notify.sh
```

---

## 六、客戶端部署（每家客戶執行）

### 6.1 前置準備

在 IPAM Server 上取得 Server 公鑰，交給客戶：

```bash
sudo cat /etc/wireguard/server.pub
```

客戶在其主機上生成金鑰對（第 4.2 節），並將**公鑰**回傳給你（IPAM 管理員）。

### 6.2 客戶端 WireGuard 設定

在客戶主機上建立 `/etc/wireguard/wg0.conf`：

```ini
[Interface]
# 此客戶分配到的隧道 IP（查客戶管理表）
Address    = 10.200.0.2/32
PrivateKey = <填入 /etc/wireguard/client.key 的內容>

# 啟動時開啟 IP 轉發，並設定 NAT 讓 IPAM Server 可路由到內網
# 將 eth0 換成客戶主機連接內網的實際網卡名稱（ip a 查看）
PostUp   = sysctl -w net.ipv4.ip_forward=1; \
           iptables -A FORWARD -i wg0 -j ACCEPT; \
           iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; \
           iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
# IPAM Server 公鑰
PublicKey           = <填入 IPAM Server 的 server.pub 內容>
# IPAM Server 的公開位址
Endpoint            = your-ipam-server.com:51820
# 只將前往 IPAM Server 隧道網段的流量走 WireGuard
AllowedIPs          = 10.200.0.1/32
# NAT 穿透保活（每 25 秒送一次 keepalive）
PersistentKeepalive = 25
```

```bash
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable wg-quick@wg0 --now

# 確認連線
sudo wg show wg0
```

### 6.3 在 IPAM Server 新增該客戶的 Peer

收到客戶公鑰後，在 IPAM Server 編輯 `/etc/wireguard/wg0.conf`：

```ini
# 新增在文件末尾
[Peer]
PublicKey  = <客戶A的 client.pub 內容>
AllowedIPs = 10.200.0.2/32, 192.168.1.0/24   # 隧道IP + 客戶實際內網
```

```bash
# 不需重啟，熱更新即可
sudo wg addconf wg0 <(wg-quick strip wg0)
# 或直接重新載入
sudo systemctl restart wg-quick@wg0
```

### 6.4 驗證連線

```bash
# 在 IPAM Server 測試能否 ping 到客戶內網的主機
ping -c 3 192.168.1.1   # 換成客戶實際的 gateway 或任意內網 IP

# 確認 WireGuard peer 有流量（latest handshake 會更新）
sudo wg show wg0
```

---

## 七、客戶管理

### 7.1 客戶分配表

| 編號 | 客戶名稱 | 隧道 IP | 客戶內網網段 | 客戶出口 IP | 客戶主機網卡 | 狀態 |
|------|---------|--------|------------|------------|------------|------|
| 001 | 範例A公司 | 10.200.0.2 | 192.168.1.0/24 | x.x.x.x | eth0 | ✓ 連線中 |
| 002 | 範例B公司 | 10.200.0.3 | 10.10.0.0/24 | x.x.x.x | ens3 | ✓ 連線中 |
| 003 | （待分配）| 10.200.0.4 | — | — | — | ✗ 未使用 |

> **注意**：「客戶內網網段」欄位各客戶不可重疊，這是本方案的前提條件。

### 7.2 新增客戶 SOP

```
□ 查分配表，取下一個可用隧道 IP（10.200.0.x）
□ 確認客戶內網網段與現有客戶不重疊
□ 請客戶在其主機上安裝 WireGuard、生成金鑰對，並回傳公鑰
□ 在 IPAM Server /etc/wireguard/wg0.conf 新增 [Peer] 區塊
□ 執行 sudo wg syncconf wg0 <(wg-quick strip wg0) 熱更新
□ 提供客戶：Server 公鑰、Server Endpoint、分配的隧道 IP
□ 客戶完成第六章部署
□ 在 IPAM Server 執行 ping 驗證
□ 更新 ufw：允許客戶出口 IP 連 port 51820/udp
□ 更新客戶分配表
```

### 7.3 移除客戶 SOP

```
□ 通知客戶停止服務
□ 客戶端執行：sudo systemctl stop wg-quick@wg0
□ 在 IPAM Server /etc/wireguard/wg0.conf 刪除該客戶的 [Peer] 區塊
□ 執行 sudo wg syncconf wg0 <(wg-quick strip wg0) 熱更新
□ 移除 ufw 規則：
    sudo ufw delete allow from <客戶出口IP> to any port 51820
□ 更新分配表：隧道 IP 標記為「已釋放」（可重用）、狀態改為「已停用」
```

### 7.4 客戶出口 IP 異動 SOP（客戶換 ISP 等情況）

```
□ 移除舊的 ufw 規則
□ 新增新出口 IP 的 ufw 規則
□ 更新客戶分配表的出口 IP 欄位
□ WireGuard 設定不需修改（WireGuard 自動重新協商）
```

---

## 八、jt-ipam 整合說明

### 零程式碼修改

本方案最大優勢：**jt-ipam Backend 完全不需要修改任何程式碼**。

WireGuard 在 OS 路由層處理路由，jt-ipam 的 `asyncssh.connect("192.168.1.10")` 完全無需感知 VPN 的存在：

```
jt-ipam 呼叫：asyncssh.connect("192.168.1.10", port=22)
OS 路由表查詢：192.168.1.0/24 → dev wg0  （WireGuard 自動處理）
封包加密後送到客戶A的 WireGuard peer
客戶A的主機解密後 NAT 到 192.168.1.10:22
```

### 唯一需要確認的事項

確認 IPAM Server 的 `wg0.conf` 中每個 `[Peer]` 的 `AllowedIPs` 包含該客戶的**完整內網網段**，路由才會正確。

---

## 九、安全性檢查清單

### 部署完成後驗證

```
□ sudo wg show wg0 確認每個 peer 有 latest handshake（代表連線正常）
□ 從 IPAM Server ping 到每家客戶的內網 gateway
□ ufw status 確認 51820/udp 只對客戶 IP 開放
□ SSH 登入測試：確認需要 Key + TOTP 驗證碼
□ fail2ban 測試：連續輸入錯誤密碼後確認被封鎖
□ auditd 測試：修改 wg0.conf 後確認有 log
□ 告警測試：SSH 登入後確認 Webhook 收到通知
□ 確認所有客戶內網網段不重疊（彙整分配表核對）
```

### 定期維護（每月）

```
□ sudo wg show wg0 確認所有 peer 狀態正常
□ 檢查 fail2ban 封鎖記錄：sudo fail2ban-client status sshd
□ 確認 unattended-upgrades 有執行：cat /var/log/unattended-upgrades/unattended-upgrades.log
□ 確認 jt-ipam 版本是否有新的安全更新（GitHub Releases）
□ auditd 有無異常 wireguard-config 變更記錄
```

---

## 十、問題排查

| 現象 | 可能原因 | 檢查指令 |
|------|---------|---------|
| wg show 顯示 peer 無 handshake | 客戶出口 IP 未在 ufw 白名單、設定錯誤 | `sudo ufw status`、`sudo journalctl -u wg-quick@wg0` |
| ping 客戶內網失敗（有 handshake）| AllowedIPs 未包含目標網段、IP forward 未啟用 | `sudo wg show wg0`、`sysctl net.ipv4.ip_forward` |
| ping 客戶內網失敗（無 handshake）| 客戶端 WireGuard 未啟動 | 在客戶端：`sudo wg show wg0` |
| 路由到錯誤的客戶 | AllowedIPs 網段有重疊 | `ip route show` 確認路由無衝突 |
| SSH MFA 驗證碼錯誤 | 時間不同步 | `timedatectl status`，確認 NTP 同步 |
| 客戶換了出口 IP 後斷線 | ufw 舊規則阻擋 | 更新 ufw 規則，WireGuard 自動重連 |

---

## 附錄：快速指令參考

```bash
# 查看 WireGuard 目前狀態（所有 peer、流量、最後握手時間）
sudo wg show wg0

# 熱更新 wg0.conf（新增/移除 peer 後執行，不斷線）
sudo wg syncconf wg0 <(wg-quick strip wg0)

# 從 IPAM Server ping 特定客戶內網
ping -c 3 192.168.1.1

# 查看 OS 路由表（確認客戶網段路由正確）
ip route show | grep wg0

# 查看 fail2ban 封鎖清單
sudo fail2ban-client status sshd

# 查看 auditd WireGuard 設定變更記錄
sudo ausearch -k wireguard-config --start recent

# 手動更新 jt-ipam
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade

# 生成新客戶金鑰對（在客戶主機上執行）
wg genkey | tee client.key | wg pubkey > client.pub
```

---

## 附錄：兩種方案選擇指引

| 條件 | 選擇 |
|------|------|
| 所有客戶網段確認不重疊 | **本文件（WireGuard）** |
| 有任何客戶網段重疊 | `remote-access-deployment.md`（SOCKS5+frp）|
| 客戶主機可安裝 WireGuard kernel module | **本文件（WireGuard）** |
| 客戶主機只有 Docker，無 kernel module 支援 | `remote-access-deployment.md`（SOCKS5+frp）|
