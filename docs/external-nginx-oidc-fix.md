# jt-ipam 外部反向代理 + OIDC 完整修正技術文件

> 適用版本：jt-ipam v0.4.111  
> IdP：Microsoft Entra ID  
> 修正日期：2026-06-10  
> Git Commit：`9514ee3`

---

## 架構概述

```
瀏覽器
  │  HTTPS
  ▼
外部 NGINX（前端機，負責 SSL 終結）
  │  HTTP
  ▼
IPAM 主機內部 NGINX（靜態檔服務 + API 路由）
  │  HTTP loopback 127.0.0.1:8000
  ▼
FastAPI 後端（uvicorn）
```

---

## 零、修改彙整

### 0.1 Repo 程式碼修改（已 commit，全新部署不需再手動修正）

| 檔案 | 修改類型 | 說明 |
|------|---------|------|
| `backend/app/api/v1/endpoints/sso.py` | Bug Fix | 新增 `import base64` / `import json`；在 OIDC callback 解析 ID Token JWT，將 `groups` 等 claim 合併進 `claims` dict |
| `frontend/src/stores/auth.ts` | Bug Fix | 新增 `loginFromOidc()` function 並 export，讓 Login.vue 可呼叫 |
| `frontend/src/views/Login.vue` | Bug Fix | 在 `onMounted` 加入 URL fragment 解析邏輯，處理 OIDC callback 帶回的 `#access_token=...` |
| `deploy/nginx/jt-ipam-external-proxy.conf` | 新增 | 外部代理模式的 nginx site 設定範本（HTTP port 80，無 HTTPS block） |
| `deploy/nginx/jt-ipam-external-proxy-snippet.conf` | 新增 | 外部代理模式的 proxy snippet（`X-Forwarded-Proto` 改用 `$http_x_forwarded_proto`） |

### 0.2 伺服器設定（環境相關，每次部署仍需手動設定）

| 位置 | 操作 | 說明 |
|------|------|------|
| `/etc/nginx/sites-available/jt-ipam` | 從 repo `deploy/nginx/jt-ipam-external-proxy.conf` 複製 | 外部代理模式 nginx site 設定 |
| `/etc/nginx/snippets/jt-ipam-proxy.conf` | 從 repo `deploy/nginx/jt-ipam-external-proxy-snippet.conf` 複製 | 修正 `X-Forwarded-Proto` |
| `/etc/jt-ipam/backend.env` | 手動填入 | 實際網域、Secret Key、DB 密碼等環境變數 |
| Azure 入口網站 | App Registration → Token Configuration | 勾選「安全性群組」，格式選「群組識別碼」 |
| jt-ipam UI | 系統設定 → SSO → OIDC | 填入正確 Redirect URI 與管理員群組 Object ID |

---

## 一、NGINX 設定修正

### 問題

系統內建 NGINX 的 port 80 block 會將所有 HTTP 請求強制 301 導向 HTTPS，導致外部反向代理轉發的流量被拒絕。

### 1.1 IPAM 主機內部 NGINX（Repo 已有範本）

安裝時從 repo 複製外部代理模式的設定：

```bash
sudo cp /opt/jt-ipam/deploy/nginx/jt-ipam-external-proxy.conf /etc/nginx/sites-available/jt-ipam
sudo cp /opt/jt-ipam/deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

**`/etc/nginx/sites-available/jt-ipam` 關鍵設計（與原設定的差異）：**

```nginx
# ── 主站：HTTP port 80，接收外部 NGINX 轉發 ──
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # 移除：原本的 HTTP → HTTPS 301 redirect
    # 移除：HSTS header（應由持有憑證的外部 NGINX 送出）
    # 保留：CSP、X-Content-Type-Options 等安全 header

    root /opt/jt-ipam/frontend/dist;
    # ...
}
# 移除：原本的 listen 443 ssl HTTPS server block
```

### 1.2 Proxy Snippet（關鍵修正）

**`/etc/nginx/snippets/jt-ipam-proxy.conf`** 的關鍵改動：

```nginx
# 原設定（錯誤）
proxy_set_header X-Forwarded-Proto $scheme;

# 修正後（正確）
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
```

> **根本原因**：本機 nginx 接收的是 HTTP，`$scheme` 恆為 `"http"`。若傳 `"http"` 給 FastAPI，後端的 `session_cookie_secure=True` 判斷協定失敗，導致 OIDC callback 500 錯誤或 Secure cookie 無法設定。改用 `$http_x_forwarded_proto` 後，外部 NGINX 傳入的 `"https"` 可完整透傳至後端。

### 1.3 外部 NGINX 設定（前端機，僅參考範本）

```nginx
server {
    listen 443 ssl http2;
    server_name ipam.your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # HSTS 在此送出（外部 NGINX 持有憑證）
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    location / {
        proxy_pass http://IPAM主機IP:80;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # 外部 NGINX 這層 $scheme = "https"

        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name ipam.your-domain.com;
    return 301 https://$host$request_uri;
}
```

---

## 二、後端環境變數修正

### 問題

`/etc/jt-ipam/backend.env` 仍保留 `.env.example` 的預設值 `ipam.example.com`，導致後端簽發的 JWT、OIDC 回呼網址均錯誤。

### 修正：`/etc/jt-ipam/backend.env`

```dotenv
APP_PUBLIC_URL=https://ipam.your-domain.com
API_PUBLIC_URL=https://ipam.your-domain.com
CORS_ORIGINS=https://ipam.your-domain.com
BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000
OIDC_REDIRECT_URI=https://ipam.your-domain.com/api/v1/auth/oidc/callback
```

套用指令：

```bash
sudo systemctl restart jt-ipam-backend
```

---

## 三、OIDC Redirect URI 卡在 `ipam.example.com`

### 問題根源

`system_config.py` 的 `get_oidc_config()` 採 **DB 優先** 策略：

```python
# 先從 .env 讀取
cfg = OidcConfig(redirect_uri=s.oidc_redirect_uri)
# 若 DB 有值，直接覆蓋 .env
if isinstance(v.get("redirect_uri"), str) and v["redirect_uri"] != "":
    setattr(cfg, "redirect_uri", v["redirect_uri"])
```

若使用者曾在 UI 儲存過含有 `ipam.example.com` 的設定，DB 值永遠優先於 `.env`。

> **注意**：此問題只發生在**升級或重新設定的現有系統**。全新部署（DB 為空）時，`.env` 值直接生效，不會遇到此問題。

### 修正方式

到 jt-ipam UI **系統設定 → SSO → OIDC**，將 Redirect URI 欄位更新為實際網域並儲存：

```
https://ipam.your-domain.com/api/v1/auth/oidc/callback
```

或直接清除 DB 記錄（讓系統完全依賴 `.env`）：

```sql
DELETE FROM system_settings WHERE key = 'oidc';
```

---

## 四、前端：OIDC 登入後停留在登入頁（程式碼 Bug）

### 問題根源

後端 OIDC callback 成功後以 HTTP 302 導向：

```
https://ipam.your-domain.com/login#access_token=<jwt>&refresh_token=<jwt>
```

但 `Login.vue` 的 `onMounted` **完全沒有解析 URL fragment 的邏輯**，token 被忽略，登入頁重新渲染，形成無限迴圈。

---

### 修正一：`frontend/src/stores/auth.ts`

> **此修改已 commit 至 repo**，全新部署不需再手動修正。

**修改位置**：`persistTokens` 為私有 function，需新增一個可 export 的入口讓 Login.vue 呼叫。

```diff
  async function fetchMe() {
    const { data } = await apiClient.get<UserMe>("/api/v1/auth/me");
    me.value = data;
  }

+ async function loginFromOidc(access: string, refresh: string): Promise<void> {
+   persistTokens({
+     access_token: access,
+     refresh_token: refresh,
+     token_type: "bearer",
+     expires_in: null,
+     mfa_required: false,
+     mfa_token: null,
+   });
+   await fetchMe();
+ }

  async function logout() {
```

```diff
  return {
    // ... 原有 exports
+   loginFromOidc,
    logout,
    clearTokens,
  };
```

> **說明**：`expires_in: null, mfa_required: false, mfa_token: null` 是必填欄位（`TokenResponse` interface 要求），需一起傳入，否則 TypeScript 編譯報錯。

---

### 修正二：`frontend/src/views/Login.vue`

> **此修改已 commit 至 repo**，全新部署不需再手動修正。

**修改位置**：`onMounted` 的第一行（在任何既有邏輯之前）。

```diff
  onMounted(async () => {
+   // OIDC callback：後端把 token 放在 URL fragment 傳回
+   const params = new URLSearchParams(window.location.hash.substring(1));
+   const oidcAccess = params.get("access_token");
+   const oidcRefresh = params.get("refresh_token");
+   if (oidcAccess) {
+     try {
+       await auth.loginFromOidc(oidcAccess, oidcRefresh ?? "");
+       // 清掉 URL 上的 token fragment，避免重新整理時重複處理
+       window.history.replaceState(null, "", window.location.pathname);
+       window.location.assign(targetAfterLogin());
+     } catch {
+       errorMsg.value = t("login.failed");
+     }
+     return;
+   }
+
    try {
      const { data } = await apiClient.get<{ realms: { label: string; value: string }[] }>("/api/v1/auth/realms");
      // ...
```

---

### 前端修改後 Build

修改後需重新 build 前端（全新部署由安裝腳本自動執行，升級時手動執行）：

```bash
cd /opt/jt-ipam/frontend
pnpm install --frozen-lockfile
pnpm build
# dist/ 由 nginx 直接服務，build 完立即生效，無需重啟 nginx
```

---

## 五、OIDC 管理員群組無法自動套用

### 問題根源

系統判斷管理員群組的邏輯（`oidc.py`）：

```python
groups_raw = claims.get(cfg.groups_claim) or []   # groups_claim 預設 "groups"
is_admin = any(g in cfg.admin_groups for g in groups)
```

`claims` 來自 `fetch_userinfo()`，呼叫的是 Microsoft Graph userinfo endpoint：

```
https://graph.microsoft.com/oidc/userinfo
```

此 endpoint 僅回傳基本欄位 `{ sub, name, email, given_name, picture }`，**不包含 `groups`**。  
`groups` claim 存在於 **ID Token** 中，需要另外解析。

---

### 修正一：`backend/app/api/v1/endpoints/sso.py`

> **此修改已 commit 至 repo**，全新部署不需再手動修正。

**修改 1 — module level import（檔案頂部，`from __future__` 之後）：**

```diff
  from __future__ import annotations

+ import base64
+ import json
  from typing import Annotated, Any
```

**修改 2 — ID Token 解析（`oidc_callback` function 內，`fetch_userinfo` 呼叫結束後）：**

```diff
      except oidc_service.OIDCError as exc:
          raise HTTPException(502, detail=str(exc)) from exc

+     # 合併 ID Token payload 中的 claims（補 userinfo 沒有的欄位，例如 Entra ID 的 groups）
+     # 安全性說明：此處不驗 ID Token 簽章，安全性依賴 TLS + state/nonce 已於前面步驟驗證
+     id_token_raw = token_data.get("id_token", "")
+     if id_token_raw:
+         try:
+             payload_b64 = id_token_raw.split(".")[1]
+             payload_b64 += "=" * (4 - len(payload_b64) % 4)
+             id_token_claims: dict[str, Any] = json.loads(
+                 base64.urlsafe_b64decode(payload_b64)
+             )
+             for k, v in id_token_claims.items():
+                 if k not in claims:
+                     claims[k] = v
+         except Exception:
+             pass  # ID Token 解析失敗不中斷流程

      try:
          user = await oidc_service.upsert_user_from_oidc(
```

> **重要**：`import` 必須放在 **module level**（檔案頂部），而不是 try 區塊內部。若放在 try 內部且 `except Exception: pass` 靜默吞掉例外，`NameError` 將完全無法被察覺，導致整個合併邏輯靜默失效。

修改後重啟後端：

```bash
sudo systemctl restart jt-ipam-backend
```

---

### 修正二：Microsoft Entra ID Token 設定（一次性設定）

**Azure Portal → App Registration → 權杖設定 → 新增群組宣告**

| 項目 | 設定值 |
|------|--------|
| 群組類型 | ☑️ **安全性群組**（此項必勾，否則 ID Token 完全不含 groups 欄位） |
| 識別碼（ID Token）格式 | **群組識別碼** |
| 存取（Access Token）格式 | **群組識別碼** |

儲存後，ID Token payload 將包含：

```json
{
  "groups": ["xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"]
}
```

---

### 修正三：jt-ipam OIDC 設定（首次安裝後在 UI 設定）

**UI：系統設定 → SSO → OIDC → 管理員群組**

填入 Entra ID 群組的 Object ID（GUID），多個群組用逗號分隔：

```
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

確認 DB 已儲存：

```bash
sudo -u postgres psql -d jt_ipam -c \
  "SELECT value->'admin_groups' FROM system_settings WHERE key='oidc';"
```

---

## 六、合併後的資料流

```
exchange_code()
  ├── access_token ──→ fetch_userinfo() ──→ { sub, email, name, ... }
  └── id_token(JWT) ──→ 解析 payload    ──→ { groups: ["guid"], iss, iat, ... }
                                                ↓ 合併（userinfo 有的不覆蓋）
                            claims = { sub, email, name, groups: ["guid"], ... }
                                                ↓
                            is_admin = "guid" in cfg.admin_groups  ✓
```

---

## 七、注意事項

| 項目 | 說明 |
|------|------|
| **Entra ID 群組超過 200 個** | 超過上限時 Entra ID 會省略 `groups` claim，改以 `_claim_names` / `_claim_sources` 提示（overage indicator），需改用 Microsoft Graph API 查詢，為獨立議題 |
| **HSTS** | 應由外部 NGINX 送出，IPAM 主機內部 NGINX 不應送出（內部走 HTTP） |
| **IdP 允許的 Redirect URI** | 須在 Entra ID App Registration → 驗證 → 新增 `https://ipam.your-domain.com/api/v1/auth/oidc/callback` |
| **DB 優先於 .env** | OIDC 所有設定（redirect_uri、admin_groups 等）若曾透過 UI 儲存，DB 值永遠覆蓋 .env；修改 .env 後需同步在 UI 儲存一次 |
| **全新部署無 DB 殘留問題** | DB 為空時 `.env` 直接生效，不存在 `ipam.example.com` 覆蓋問題 |

---

## 八、全新部署操作步驟

```bash
# 1. clone repo 到伺服器
sudo git clone https://你的repo /opt/jt-ipam

# 2. 執行安裝腳本（自動跑 pnpm build、建 DB、設定 systemd）
cd /opt/jt-ipam
sudo ./scripts/install-debian.sh \
    --tls-mode nginx \
    --public-fqdn ipam.your-domain.com

# 3. 套用外部代理模式的 nginx 設定（repo 已內建）
sudo cp deploy/nginx/jt-ipam-external-proxy.conf /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx

# 4. 確認 backend.env 的網域設定正確
sudo nano /etc/jt-ipam/backend.env
# 修改：APP_PUBLIC_URL / API_PUBLIC_URL / CORS_ORIGINS / OIDC_REDIRECT_URI
sudo systemctl restart jt-ipam-backend
```

完成後在 jt-ipam UI 完成一次性設定：
- 系統設定 → SSO → OIDC → 填入 Client ID / Secret / Issuer / Redirect URI
- 系統設定 → SSO → 管理員群組 → 填入 Entra ID 群組 Object ID

---

## 九、升級流程（已有運行中的機器）

```bash
cd /opt/jt-ipam
sudo git pull --ff-only

# 重新 build 前端（包含 Login.vue / auth.ts 修正）
cd frontend
sudo -u jtipam pnpm install --frozen-lockfile
sudo -u jtipam pnpm build

# 重啟後端
sudo systemctl restart jt-ipam-backend
sudo systemctl reload nginx
```

或使用內建升級腳本（git pull + build + restart 全包）：

```bash
sudo ./scripts/jt-ipam.sh upgrade
```

---

## 十、完整驗證清單

```bash
# 1. NGINX 監聽正確 port
ss -tlnp | grep nginx
# 預期：80, 8088（無 443，SSL 由外部 NGINX 負責）

# 2. FastAPI 綁定 loopback
ss -tlnp | grep 8000
# 預期：127.0.0.1:8000

# 3. 後端服務正常
sudo systemctl status jt-ipam-backend

# 4. OIDC 設定確認（DB 值）
sudo -u postgres psql -d jt_ipam -c \
  "SELECT value->'redirect_uri', value->'admin_groups' FROM system_settings WHERE key='oidc';"

# 5. 前端 build 是最新版本（確認包含 Login.vue 修正）
ls -la /opt/jt-ipam/frontend/dist/index.html
```

| 測試項目 | 預期結果 |
|---------|---------|
| 瀏覽器開啟 `https://ipam.your-domain.com` | 正常顯示登入頁 |
| 點擊 OIDC 登入，跳轉網域 | 為 `ipam.your-domain.com`（非 `ipam.example.com`） |
| OIDC 登入後 | 直接進入系統，不停留在登入頁 |
| 管理員群組成員登入後 | 使用者列表「管理者」欄位顯示為啟用 |
