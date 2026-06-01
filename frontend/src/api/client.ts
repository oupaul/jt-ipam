import axios, { AxiosError } from "axios";

/**
 * 統一的 API client。
 *
 * OWASP 對應：
 * - A01：401/403 集中處理
 * - A05：withCredentials 預設 false(同源由 nginx 反代)
 * - A09：每個 request 帶 X-Request-ID 與後端 trace 串接
 */
function generateRequestId(): string {
  const arr = crypto.getRandomValues(new Uint8Array(16));
  arr[6] = (arr[6] & 0x0f) | 0x40;
  arr[8] = (arr[8] & 0x3f) | 0x80;
  const hex = Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/",
  timeout: 15_000,
  withCredentials: false,
});

apiClient.interceptors.request.use((config) => {
  config.headers.set("X-Request-ID", generateRequestId());
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// 一次同時收到多個 401 時，只觸發一次 refresh；其它等同一個 promise
let refreshingPromise: Promise<string | null> | null = null;

async function tryRefreshToken(): Promise<string | null> {
  if (refreshingPromise) return refreshingPromise;
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return null;
  refreshingPromise = (async () => {
    try {
      // 用 axios 裸請求避免拉 interceptor 連鎖
      const resp = await axios.post("/api/v1/auth/refresh",
        { refresh_token: refreshToken },
        { headers: { "X-Request-ID": generateRequestId() }, timeout: 10_000 });
      const data = resp.data as { access_token?: string; refresh_token?: string };
      if (data?.access_token) {
        localStorage.setItem("access_token", data.access_token);
        if (data.refresh_token) {
          localStorage.setItem("refresh_token", data.refresh_token);
        }
        return data.access_token;
      }
      return null;
    } catch {
      return null;
    } finally {
      // 下次 401 又可以觸發新的 refresh(10s 內的併發共用同一個)
      setTimeout(() => { refreshingPromise = null; }, 0);
    }
  })();
  return refreshingPromise;
}

apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const config: any = error.config ?? {};
    // 401 嘗試 refresh 一次 (避免 refresh 自己再 refresh 無限迴圈)
    if (
      error.response?.status === 401 &&
      !config._retried &&
      // 不對 refresh 端點自己重試
      !(typeof config.url === "string" && config.url.includes("/auth/refresh"))
    ) {
      const newToken = await tryRefreshToken();
      if (newToken) {
        config._retried = true;
        config.headers = config.headers ?? {};
        config.headers["Authorization"] = `Bearer ${newToken}`;
        return apiClient.request(config);
      }
      // refresh 失敗 → 清 token 跳 /login
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.assign(`/login?next=${next}`);
      }
    }
    return Promise.reject(error);
  },
);
