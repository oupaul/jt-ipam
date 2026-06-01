/**
 * Per-user column visibility preference.
 *
 * 從 /api/v1/me/preferences 拉 table_columns，本機 localStorage 快取一份。
 * 切換可見欄位時馬上更新 UI + 後台 patch；patch 失敗回滾。
 *
 * 用法 (在 view 內)：
 *
 *   const { visibleKeys, setVisible, allColumns, isVisible } = useColumnPrefs(
 *     "addresses",
 *     ["live", "ip", "hostname", "mac", "state", "discovery_source"],   // 全部可選欄位 key
 *     ["live", "ip", "hostname", "state"],                              // 預設可見
 *   );
 *
 *   const filteredCols = computed(() => allColumns.filter((c) => isVisible(c.key)));
 */

import { ref, watch } from "vue";
import { apiClient } from "@/api/client";

const LS_KEY = "jt-ipam:table_columns";

// 全局 cache(一個 SPA session 內共用一份)
const cache = ref<Record<string, string[]>>({});
let loaded = false;

async function loadCache(): Promise<void> {
  if (loaded) return;
  loaded = true;
  // 1. 先讀 localStorage(避免登入後第一次拉時的閃爍)
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) cache.value = JSON.parse(raw) ?? {};
  } catch {}
  // 2. 再拉後端覆寫 (authoritative)
  try {
    const { data } = await apiClient.get<{ table_columns: Record<string, string[]> | null }>(
      "/api/v1/me/preferences",
    );
    if (data?.table_columns) {
      cache.value = data.table_columns;
      localStorage.setItem(LS_KEY, JSON.stringify(cache.value));
    }
  } catch {
    // 沒登入 / API 失敗 → 用 localStorage 版本就好
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
async function persist(): Promise<void> {
  localStorage.setItem(LS_KEY, JSON.stringify(cache.value));
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await apiClient.patch("/api/v1/me/preferences", { table_columns: cache.value });
    } catch {
      // 後端失敗不回滾 UI；下次重整時會以後端為準 (loadCache)
    }
  }, 400);
}

export function useColumnPrefs(
  tableKey: string,
  allKeys: string[],
  defaultVisible: string[],
) {
  void loadCache();
  const initial = cache.value[tableKey] ?? defaultVisible;
  const visibleKeys = ref<string[]>([...initial]);

  // 同步 cache → local(其他 view 改了，這 view 不會立刻看到，但下次 mount 會)
  watch(
    () => cache.value[tableKey],
    (v) => { if (v) visibleKeys.value = [...v]; },
  );

  function isVisible(key: string): boolean {
    return visibleKeys.value.includes(key);
  }

  function setVisible(keys: string[]) {
    // 過濾掉不在 allKeys 裡的 (防 stale)
    const filtered = keys.filter((k) => allKeys.includes(k));
    visibleKeys.value = filtered;
    cache.value[tableKey] = filtered;
    void persist();
  }

  function reset() {
    setVisible([...defaultVisible]);
  }

  return { visibleKeys, isVisible, setVisible, reset, allKeys };
}
