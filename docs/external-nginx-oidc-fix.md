# jt-ipam 外部反向代理 + OIDC 完整修正技術文件

> 適用版本：jt-ipam v0.4.111  
> IdP：Microsoft Entra ID  
> 修正日期：2026-06-09

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

## 一、NGINX 設定修正

### 問題

系統內建 NGINX 的 port 80 block 會將所有 HTTP 請求強制 301 導向 HTTPS，導致外部反向代理轉發的流量被拒絕。

### 修正：IPAM 主機內部 NGINX

**檔案路徑：`/etc/nginx/sites-available/jt-ipam`**

移除原本的 HTTPS server block，改為 HTTP port 80 接收外部 NGINX 轉發的流量：

```nginx
server_tokens off;

limit_req_zone $binary_remote_addr zone=login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=api:10m   rate=1200r/m;

map $sent_http_content_type $expires {
    default                 off;
    text/html               -1;
    ~image/                 30d;
    ~font/                  30d;
    application/javascript  7d;
    text/css                7d;
}

limit_req_status 429;

# ── Graylog DSV（保留原設定）──
server {
    listen 8088;
    listen [::]:8088;
    server_name _;

    location /api/v1/lookup/ {
        limit_req zone=api burst=80 nodelay;
        proxy_pass http://127.0.0.1:8000;
        include /etc/nginx/snippets/jt-ipam-proxy.conf;
    }
    location / { return 404; }
}

# ── 主站：HTTP port 80，接收外部 NGINX 轉發 ──
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # HSTS 移除（必須由持有憑證的外部 NGINX 負責送出）
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.tile.openstreetmap.org; font-src 'self' data:; connect-src 'self'; frame-src 'self' https://www.openstreetmap.org https://www.google.com https://maps.google.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self';" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=(), payment=(), usb=()" always;
    add_header Cross-Origin-Opener-Policy "same-origin" always;

    root /opt/jt-ipam/frontend/dist;
    index index.html;
    expires $expires;

    location = /healthz {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok";
    }

    location /api/phpipam/user/ {
        limit_req zone=login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        include /etc/nginx/snippets/jt-ipam-proxy.conf;
    }

    location /api/ {
        limit_req zone=api burst=80 nodelay;
        proxy_pass http://127.0.0.1:8000;
        include /etc/nginx/snippets/jt-ipam-proxy.conf;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~ /\.(env|git|svn|hg|DS_Store) {
        deny all;
        access_log off;
        return 404;
    }

    client_max_body_size 16m;
    client_body_timeout 30s;
    client_header_timeout 30s;
}
```

### 修正：Proxy Snippet

**檔案路徑：`/etc/nginx/snippets/jt-ipam-proxy.conf`**

將 `X-Forwarded-Proto` 改為向上游取值，確保後端感知到外部是 HTTPS 連線：

```nginx
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;   # 原為 $scheme，改為透傳上游值
proxy_set_header X-Request-ID      $request_id;
proxy_read_timeout    30s;
proxy_connect_timeout  5s;
proxy_buffering on;
```

> **說明**：原本 `$scheme` 在內部 NGINX 這層為 `"http"`，會導致 FastAPI 的 `session_cookie_secure=True` 判斷失誤。改用 `$http_x_forwarded_proto` 後，外部 NGINX 傳入的 `https` 值可完整透傳至後端。

### 外部 NGINX 設定（前端機）

```nginx
server {
    listen 443 ssl http2;
    server_name ipam.mis4o.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    location / {
        proxy_pass http://IPAM主機IP:80;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name ipam.mis4o.com;
    return 301 https://$host$request_uri;
}
```

套用指令：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 二、後端環境變數修正

### 問題

`/etc/jt-ipam/backend.env` 仍保留 `.env.example` 的預設值 `ipam.example.com`，導致後端簽發的 JWT、OIDC 回呼網址均錯誤。

### 修正：`/etc/jt-ipam/backend.env`

```dotenv
APP_PUBLIC_URL=https://ipam.mis4o.com
API_PUBLIC_URL=https://ipam.mis4o.com
CORS_ORIGINS=https://ipam.mis4o.com
BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000
OIDC_REDIRECT_URI=https://ipam.mis4o.com/api/v1/auth/oidc/callback
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

### 修正方式

到 jt-ipam UI **系統設定 → SSO → OIDC**，將 Redirect URI 欄位更新為實際網域並儲存：

```
https://ipam.mis4o.com/api/v1/auth/oidc/callback
```

或直接清除 DB 記錄（讓系統完全依賴 `.env`）：

```sql
DELETE FROM system_settings WHERE key = 'oidc';
```

---

## 四、前端：OIDC 登入後停留在登入頁（前端 Bug）

### 問題根源

後端 OIDC callback 成功後以 HTTP 302 導向：

```
https://ipam.mis4o.com/login#access_token=<jwt>&refresh_token=<jwt>
```

但 `Login.vue` 的 `onMounted` **完全沒有解析 URL fragment 的邏輯**，token 被忽略，登入頁重新渲染，形成無限迴圈。

### 修正一：`frontend/src/stores/auth.ts`

新增 `loginFromOidc` function 並加入 return export：

```typescript
// 在 logout function 之前加入
async function loginFromOidc(access: string, refresh: string): Promise<void> {
  persistTokens({
    access_token: access,
    refresh_token: refresh,
    token_type: "bearer",
    expires_in: null,
    mfa_required: false,
    mfa_token: null,
  });
  await fetchMe();
}

// return 區塊加入
return {
  // ... 原有 exports
  loginFromOidc,
};
```

### 修正二：`frontend/src/views/Login.vue`

在 `onMounted` 最前面加入 OIDC fragment 處理：

```typescript
onMounted(async () => {
  // OIDC callback：後端把 token 放在 URL fragment 傳回
  const params = new URLSearchParams(window.location.hash.substring(1));
  const oidcAccess = params.get("access_token");
  const oidcRefresh = params.get("refresh_token");
  if (oidcAccess) {
    try {
      await auth.loginFromOidc(oidcAccess, oidcRefresh ?? "");
      // 清掉 URL 上的 token fragment，避免重新整理時重複處理
      window.history.replaceState(null, "", window.location.pathname);
      window.location.assign(targetAfterLogin());
    } catch {
      errorMsg.value = t("login.failed");
    }
    return;
  }

  // 正常流程：載入 realms
  try {
    const { data } = await apiClient.get<{ realms: { label: string; value: string }[] }>("/api/v1/auth/realms");
    if (data.realms?.length) {
      realms.value = data.realms;
      localStorage.setItem("jtipam.realms", JSON.stringify(data.realms));
    }
  } catch { /* 預設只有本機 */ }
});
```

### 重新 Build 並部署

```bash
cd /opt/jt-ipam/frontend
npm run build
# dist/ 由 NGINX 自動服務，無需額外操作
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

### 修正一：`backend/app/api/v1/endpoints/sso.py`

在 `fetch_userinfo` 之後加入 ID Token 解析與合併邏輯：

```python
# 在 fetch_userinfo 的 except 區塊結束後加入

    # 合併 ID Token payload（補 userinfo 沒有的欄位，例如 Entra ID 的 groups）
    # 安全性說明：此處不驗 ID Token 簽章，安全性依賴 TLS + state/nonce 已於前面步驟驗證
    id_token_raw = token_data.get("id_token", "")
    if id_token_raw:
        try:
            import base64 as _b64, json as _json   # 確保 import 在 try 內，避免環境差異
            payload_b64 = id_token_raw.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            id_token_claims: dict = _json.loads(
                _b64.urlsafe_b64decode(payload_b64)
            )
            for k, v in id_token_claims.items():
                if k not in claims:
                    claims[k] = v
        except Exception:
            pass  # ID Token 解析失敗不中斷流程
```

> **重點**：`import` 必須放在 `try` 區塊**內部**。若放在外部且模組未在 file level 匯入，`except Exception: pass` 會靜默吞掉 `NameError`，導致合併邏輯完全失效（`claims` 不會有任何 ID Token 欄位）。

套用後重啟：

```bash
sudo systemctl restart jt-ipam-backend
```

### 修正二：Microsoft Entra ID Token 設定

**Azure Portal → App Registration → 權杖設定 → 新增群組宣告**

| 項目 | 設定值 |
|------|--------|
| 群組類型 | ☑️ **安全性群組** |
| 識別碼（ID Token）格式 | **群組識別碼** |
| 存取（Access Token）格式 | **群組識別碼** |

儲存後，ID Token payload 將包含：

```json
{
  "groups": ["xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"]
}
```

### 修正三：jt-ipam OIDC 設定

**UI：系統設定 → SSO → OIDC → 管理員群組**

填入 Entra ID 群組的 Object ID（GUID），多個群組用逗號分隔：

```
e069e8b4-34b5-41cd-a78c-36dca25f4f73
```

確認 DB 已儲存：

```bash
sudo -u postgres psql -d jt_ipam -c "SELECT value->'admin_groups' FROM system_settings WHERE key='oidc';"
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
| **IdP 允許的 Redirect URI** | 須在 Entra ID App Registration → 驗證 → 新增 `https://ipam.mis4o.com/api/v1/auth/oidc/callback` |
| **DB 優先於 .env** | OIDC 所有設定（redirect_uri、admin_groups 等）若曾透過 UI 儲存，DB 值永遠覆蓋 .env；修改 .env 後需同步在 UI 儲存一次 |

---

## 八、完整驗證清單

```bash
# 1. NGINX 監聽正確 port
ss -tlnp | grep nginx
# 預期：80, 8088

# 2. FastAPI 綁定 loopback
ss -tlnp | grep 8000
# 預期：127.0.0.1:8000

# 3. 後端服務正常
sudo systemctl status jt-ipam-backend

# 4. OIDC 設定確認
sudo -u postgres psql -d jt_ipam -c \
  "SELECT value->'redirect_uri', value->'admin_groups' FROM system_settings WHERE key='oidc';"

# 5. 前端 build 是最新版本
ls -la /opt/jt-ipam/frontend/dist/index.html
```

| 測試項目 | 預期結果 |
|---------|---------|
| 瀏覽器開啟 `https://ipam.mis4o.com` | 正常顯示登入頁 |
| 點擊 OIDC 登入，跳轉網域 | 為 `ipam.mis4o.com`（非 `ipam.example.com`） |
| OIDC 登入後 | 直接進入系統，不停留在登入頁 |
| 管理員群組成員登入後 | 使用者列表「管理者」欄位顯示為啟用 |
