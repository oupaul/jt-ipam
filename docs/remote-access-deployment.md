# jt-ipam 遠端連線安全架構 — 技術部署文件

**版本**：1.0　　**日期**：2026-06-23　　**適用環境**：Ubuntu 22.04 LTS

---

## 一、整體架構說明

### 架構概念

```
使用者瀏覽器
  │ WebSocket（加密）
  ▼
jt-ipam Backend（IPAM Server）
  │ asyncssh → SOCKS5 proxy at 127.0.0.1:10001
  ▼
frps（反向隧道伺服器）← mTLS 反向隧道 ── frpc（客戶端）
                                              │
                                         microsocks（SOCKS5）
                                              │ 直接 TCP
                                         目標主機（192.168.1.x）
```

### 關鍵設計原則

- **客戶端主動連出**：frpc 從客戶內部向外建立反向隧道，客戶不需開放任何 inbound port
- **重疊網段天然隔離**：每家客戶的 SOCKS5 proxy 在其自己的內網，192.168.1.0/24 重疊完全不衝突
- **SOCKS5 port 只綁本機**：`proxyBindAddr = "127.0.0.1"`，外部無法直接存取
- **mTLS 雙向憑證**：每家客戶持有獨立憑證，洩漏時可單獨 revoke

### 連線流程

1. 使用者在 jt-ipam 點擊 SSH/RDP/VNC
2. Backend 查詢目標 IP 所屬 subnet 的 `socks5_port`（例如 10001）
3. Backend 透過 `127.0.0.1:10001`（該客戶的 SOCKS5）建立連線
4. SOCKS5 流量經由 frp 反向隧道到達客戶端的 microsocks container
5. microsocks 在客戶內網直接 TCP 連到目標主機

---

## 二、元件清單

| 元件 | 版本 | 用途 | 部署位置 |
|------|------|------|---------|
| frps | 0.61+ | 反向隧道伺服器端 | IPAM Server（Docker）|
| frpc | 0.61+ | 反向隧道客戶端 | 各客戶主機（Docker）|
| microsocks | latest | SOCKS5 輕量 Proxy | 各客戶主機（Docker）|
| mTLS CA | — | 憑證簽發與驗證 | IPAM Server（CA 私鑰離線保管）|
| ufw | 系統內建 | 防火牆 | IPAM Server |
| fail2ban | 系統套件 | 暴力破解防護 | IPAM Server |
| auditd | 系統套件 | 系統操作稽核 | IPAM Server |
| Google Authenticator | PAM 模組 | SSH 雙因素驗證 | IPAM Server |

---

## 三、環境需求

### IPAM Server

- OS：Ubuntu 22.04 LTS
- Docker + Docker Compose v2
- 公開固定 IP
- 開放 port：`7000/tcp`（frps，限客戶來源 IP）、`443/tcp`（jt-ipam Web）

### 客戶端主機

- OS：Linux（Ubuntu 20.04+ 建議）
- Docker + Docker Compose v2
- 只需**出站** TCP 到 IPAM Server port 7000
- 無需任何入站 port 開放

---

## 四、憑證管理（mTLS）

所有 frp 通訊使用 mTLS 雙向憑證驗證。每家客戶持有獨立憑證，洩漏時可單獨 revoke，不影響其他客戶。

### 4.1 建立 CA（執行一次，CA 私鑰離線保管）

```bash
mkdir -p /opt/frp-pki/{ca,server,clients}
cd /opt/frp-pki

# CA 金鑰（加密保護）與憑證（10 年）
openssl genrsa -aes256 -out ca/ca.key 4096
openssl req -new -x509 -days 3650 -key ca/ca.key \
  -out ca/ca.crt \
  -subj "/O=YourCompany/CN=jt-ipam-CA"
```

> **重要**：`ca.key` 完成後應移至離線裝置（USB 加密磁碟）保管，IPAM Server 上只保留 `ca.crt`。

### 4.2 建立 Server 憑證（IPAM Server 用）

```bash
openssl genrsa -out server/server.key 2048
openssl req -new -key server/server.key \
  -out server/server.csr \
  -subj "/CN=your-ipam-server.com"
openssl x509 -req -days 3650 \
  -in server/server.csr \
  -CA ca/ca.crt -CAkey ca/ca.key -CAcreateserial \
  -out server/server.crt
```

### 4.3 建立客戶端憑證（每新增一家客戶執行）

```bash
# 將 CLIENT 替換為客戶代號（例如 customer-a）
CLIENT="customer-a"

openssl genrsa -out clients/${CLIENT}.key 2048
openssl req -new -key clients/${CLIENT}.key \
  -out clients/${CLIENT}.csr \
  -subj "/O=YourCompany/CN=${CLIENT}"
openssl x509 -req -days 365 \
  -in clients/${CLIENT}.csr \
  -CA ca/ca.crt -CAkey ca/ca.key -CAcreateserial \
  -out clients/${CLIENT}.crt

# 打包交付給客戶（以加密方式傳送，勿用 email 明文）
tar czf ${CLIENT}-certs.tar.gz \
  -C clients/ ${CLIENT}.crt ${CLIENT}.key \
  -C /opt/frp-pki/ca/ ca.crt
```

### 4.4 憑證管理規範

| 項目 | 規範 |
|------|------|
| CA 私鑰 | 離線加密保管，不存放於 IPAM Server |
| Server 憑證 | `/opt/frps/certs/`，權限 600 |
| 客戶端憑證 | 加密方式交付，交付後刪除本機副本 |
| 客戶端有效期 | 365 天，到期前 30 天更新（見第七章）|

---

## 五、IPAM Server 部署

### 5.1 frps 部署

```bash
mkdir -p /opt/frps/{certs,config}
cd /opt/frps

# 複製憑證
cp /opt/frp-pki/server/server.crt certs/
cp /opt/frp-pki/server/server.key certs/
cp /opt/frp-pki/ca/ca.crt        certs/
chmod 600 certs/*.key
```

建立 `/opt/frps/config/frps.toml`：

```toml
bindPort = 7000

# mTLS：強制雙向憑證驗證
transport.tls.force = true
transport.tls.certFile      = "/etc/frp/certs/server.crt"
transport.tls.keyFile       = "/etc/frp/certs/server.key"
transport.tls.trustedCaFile = "/etc/frp/certs/ca.crt"

# SOCKS5 port 只綁本機，外部無法直接存取
proxyBindAddr = "127.0.0.1"

# 只允許轉發此 port 範圍（一家客戶一個 port）
allowPorts = [
  { start = 10001, end = 10100 }
]

log.to    = "/var/log/frps.log"
log.level = "info"
```

建立 `/opt/frps/docker-compose.yml`：

```yaml
services:
  frps:
    image: snowdreamtech/frps:0.61.0
    restart: unless-stopped
    volumes:
      - ./config/frps.toml:/etc/frp/frps.toml:ro
      - ./certs:/etc/frp/certs:ro
      - frps-log:/var/log
    ports:
      - "7000:7000"

volumes:
  frps-log:
```

```bash
docker compose up -d
docker compose logs -f   # 確認啟動無錯誤
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

# frps：每新增一家客戶加一條
sudo ufw allow from <客戶A出口IP> to any port 7000 proto tcp
sudo ufw allow from <客戶B出口IP> to any port 7000 proto tcp
# ... 依此類推

sudo ufw enable
sudo ufw status numbered
```

---

### 5.3 主機安全加固

#### SSH 強化

```bash
sudo nano /etc/ssh/sshd_config
```

確認以下設定（修改後存檔）：

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

建立 `/etc/fail2ban/jail.local`：

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

`/etc/apt/apt.conf.d/50unattended-upgrades` 確認：

```
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Mail "<你的信箱>";
```

#### SSH MFA（TOTP 雙因素驗證）

```bash
sudo apt install libpam-google-authenticator -y

# 用管理帳號身份執行，掃描 QR code 加入 Authenticator App
google-authenticator
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

> 登入時需同時提供 SSH Key + TOTP 驗證碼。

#### auditd 稽核

```bash
sudo apt install auditd -y

sudo auditctl -w /opt/jt-ipam         -p wa -k jt-ipam-changes
sudo auditctl -w /opt/frps            -p wa -k frps-changes
sudo auditctl -w /etc/ssh/sshd_config -p wa -k ssh-config
sudo auditctl -w /etc/sudoers         -p wa -k sudoers-changes

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

`/etc/pam.d/sshd` 加入（auth 段之後）：

```
session optional pam_exec.so /etc/ssh/login-notify.sh
```

---

## 六、客戶端部署（每家客戶執行）

### 6.1 前置準備

在 IPAM Server 簽發該客戶憑證（第 4.3 節），並以加密方式交付以下三個檔案：

- `customer-x.crt`
- `customer-x.key`
- `ca.crt`

### 6.2 客戶端 Docker 設定

在客戶主機上建立目錄並放入憑證：

```bash
mkdir -p /opt/ipam-connector/certs
cd /opt/ipam-connector

# 解壓收到的憑證包
tar xzf customer-x-certs.tar.gz -C certs/
chmod 600 certs/*.key
```

建立 `/opt/ipam-connector/frpc.toml`（**remotePort 每家客戶唯一**）：

```toml
serverAddr = "your-ipam-server.com"
serverPort = 7000

transport.tls.enable        = true
transport.tls.certFile      = "/etc/frp/certs/customer-x.crt"
transport.tls.keyFile       = "/etc/frp/certs/customer-x.key"
transport.tls.trustedCaFile = "/etc/frp/certs/ca.crt"

[[proxies]]
name       = "socks5-customer-x"
type       = "tcp"
localIP    = "socks5"
localPort  = 1080
remotePort = 10001          # ← 查客戶管理表取得，每家不同
```

建立 `/opt/ipam-connector/docker-compose.yml`：

```yaml
services:
  socks5:
    image: serjs/go-socks5-proxy:latest
    restart: unless-stopped
    networks:
      - internal

  frpc:
    image: snowdreamtech/frpc:0.61.0
    restart: unless-stopped
    volumes:
      - ./frpc.toml:/etc/frp/frpc.toml:ro
      - ./certs:/etc/frp/certs:ro
    networks:
      - internal
    depends_on:
      - socks5

networks:
  internal:
    driver: bridge
```

```bash
docker compose up -d
docker compose logs frpc   # 確認出現 "login to server success"
```

### 6.3 在 IPAM Server 驗證連線

```bash
# 應回傳客戶端的出口 IP（而非 IPAM Server 的 IP）
curl --socks5 127.0.0.1:10001 http://ifconfig.me
```

---

## 七、客戶管理

### 7.1 客戶編號分配表

| 編號 | 客戶名稱 | SOCKS5 Port | frp CN | 憑證到期日 | 客戶出口 IP | 狀態 |
|------|---------|------------|--------|-----------|------------|------|
| 001 | 範例A公司 | 10001 | customer-a | 2027-06-23 | x.x.x.x | ✓ 連線中 |
| 002 | 範例B公司 | 10002 | customer-b | 2027-06-23 | x.x.x.x | ✓ 連線中 |
| 003 | （待分配）| 10003 | — | — | — | ✗ 未使用 |

> 建議維護於試算表並設定憑證到期前 30 天的行事曆提醒。

### 7.2 新增客戶 SOP

```
□ 查分配表，取下一個可用 port
□ 執行第 4.3 節簽發憑證（CLIENT="customer-x"）
□ 以加密方式將憑證包交付客戶
□ 客戶端執行第六章部署步驟
□ 在 IPAM Server 執行 curl 驗證
□ 更新 ufw：允許客戶出口 IP 連 port 7000
□ 更新客戶分配表（port、CN、到期日、出口 IP、狀態）
□ jt-ipam DB 中對應 subnets 設定 socks5_port（整合實作後）
```

### 7.3 移除客戶 SOP

```
□ 通知客戶停止服務
□ 客戶端執行：docker compose down
□ 在 IPAM Server 移除 ufw 規則：
    sudo ufw delete allow from <客戶出口IP> to any port 7000
□ 更新分配表：port 標記為「已釋放」（可重用）、狀態改為「已停用」
□ 清除 jt-ipam DB 中對應的 socks5_port
□ 紀錄停用日期
```

### 7.4 憑證更新 SOP（每年，到期前 30 天執行）

```
□ 重新執行第 4.3 節（相同 CLIENT 代號）
□ 以加密方式交付新憑證包
□ 客戶端：
    cp new-certs/* /opt/ipam-connector/certs/
    docker compose restart frpc
□ 驗證連線正常（curl 測試）
□ 更新分配表的憑證到期日
```

---

## 八、jt-ipam 整合說明（待實作，目前不修改程式碼）

### 8.1 資料庫變更

`subnets` 表新增欄位：

```sql
ALTER TABLE subnets ADD COLUMN socks5_port INTEGER NULL;
-- NULL  = 直連（預設，不需 SOCKS5）
-- 10001~10100 = 透過對應客戶的 SOCKS5 proxy
```

### 8.2 後端修改範圍

**檔案**：`backend/app/api/v1/endpoints/ssh_console.py`

邏輯變更（約 20 行）：
1. 取得目標 IP 所屬 subnet 的 `socks5_port`
2. 若有值，使用 `python-socks` 建立 SOCKS5 socket 後傳入 `asyncssh.connect(sock=...)`
3. 若為 NULL，維持現有直連邏輯不變

新增 Python 依賴：`python-socks`

### 8.3 前端修改範圍

**無需修改**。使用者體驗與現有完全一致（點擊 SSH/RDP/VNC 按鈕直接連線）。

---

## 九、安全性檢查清單

### 部署完成後驗證

```
□ frps log 確認只有合法客戶 CN 連入（無陌生 CN）
□ ufw status 確認 port 7000 只對客戶 IP 開放
□ 從外部嘗試連 IPAM Server:10001 確認無法連通
□ SSH 登入測試：確認需要 Key + TOTP 驗證碼
□ fail2ban 測試：連續輸入錯誤密碼後確認被封鎖
□ auditd 測試：修改 /opt/jt-ipam 設定檔後確認有 log
□ 告警測試：SSH 登入後確認 Webhook 收到通知
□ 憑證有效期確認：openssl x509 -in client.crt -noout -dates
```

### 定期維護（每月）

```
□ 檢查 frps log 有無異常連線嘗試：docker compose -f /opt/frps/docker-compose.yml logs
□ 檢查 fail2ban 封鎖記錄：sudo fail2ban-client status sshd
□ 確認 unattended-upgrades 有執行：cat /var/log/unattended-upgrades/unattended-upgrades.log
□ 確認 jt-ipam 版本是否有新的安全更新（GitHub Releases）
□ 檢查憑證到期日：下月是否有需要更新的客戶
```

---

## 十、問題排查

| 現象 | 可能原因 | 檢查指令 |
|------|---------|---------|
| frpc 無法連到 frps | 客戶出口 IP 未在 ufw 白名單、憑證錯誤 | `sudo ufw status`、`docker compose logs frpc` |
| frps log 顯示 TLS 錯誤 | 憑證 CN 不符或 CA 不對 | `openssl verify -CAfile ca.crt client.crt` |
| curl SOCKS5 測試失敗 | container 未正常啟動 | `docker compose ps`、`docker compose logs` |
| SSH 連線 timeout | 目標主機不可達（防火牆）| 在客戶端主機直接 `ping <目標IP>` |
| MFA 驗證碼錯誤 | 時間不同步 | `timedatectl status`，確認 NTP 同步 |
| 登入告警未收到 | Webhook URL 錯誤或網路問題 | 手動執行 `/etc/ssh/login-notify.sh` 測試 |

---

## 附錄：快速指令參考

```bash
# 查看 frps 目前連線狀態
docker compose -f /opt/frps/docker-compose.yml logs --tail=50

# 測試特定客戶的 SOCKS5（port 依客戶而定）
curl --socks5 127.0.0.1:10001 http://ifconfig.me

# 查看 fail2ban 封鎖清單
sudo fail2ban-client status sshd

# 查看 auditd 近期稽核記錄
sudo ausearch -k jt-ipam-changes --start recent

# 手動更新 jt-ipam
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade

# 檢查憑證到期日
openssl x509 -in /opt/frp-pki/clients/customer-a.crt -noout -dates
```
