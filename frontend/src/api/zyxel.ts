import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

// Zyxel 防火牆整合（Beta，實驗性）。路徑帶 /api/v1 前綴（baseURL 為 /）。
// Standalone ZLD 機種沒有 REST API，走 SSH CLI —— 無實機可驗，格式可能需依實際輸出調整。

export interface ZyxelFirewall {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  enabled: boolean;
  sync_arp: boolean;
  sync_dhcp: boolean;
  sync_policies: boolean;
  sync_nat: boolean;
  sync_addresses: boolean;
  sync_interval_seconds: number;
  description: string | null;
  scope_subnet_ids: string[] | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface ZyxelWrite {
  name: string;
  host: string;
  port?: number;
  username: string;
  password?: string;
  enabled?: boolean;
  sync_arp?: boolean;
  sync_dhcp?: boolean;
  sync_policies?: boolean;
  sync_nat?: boolean;
  sync_addresses?: boolean;
  sync_interval_seconds?: number;
  description?: string;
  scope_subnet_ids?: string[];
}

/** 連線診斷：逐指令回報是否可執行、原始輸出片段（無實機開發，用來核對解析器） */
export interface ZyxelDiagnosis {
  host: string;
  ok_count: number;
  checks: { command: string; label: string; ok: boolean; sample?: string; error?: string }[];
}

export interface ZyxelPolicy {
  id: string; rule_number: string; name: string | null;
  status: string | null; action: string | null;
  from_zone: string | null; to_zone: string | null;
  source: string | null; destination: string | null;
  service: string | null; description: string | null;
}

export interface ZyxelAddressObject {
  id: string; name: string; obj_type: string | null; value: string | null;
}

export async function listZyxel(): Promise<Paginated<ZyxelFirewall>> {
  const { data } = await apiClient.get<Paginated<ZyxelFirewall>>("/api/v1/zyxel", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}

export async function createZyxel(p: ZyxelWrite): Promise<ZyxelFirewall> {
  const { data } = await apiClient.post<ZyxelFirewall>("/api/v1/zyxel", p);
  return data;
}

export async function updateZyxel(id: string, p: Partial<ZyxelWrite>): Promise<ZyxelFirewall> {
  const { data } = await apiClient.patch<ZyxelFirewall>(`/api/v1/zyxel/${id}`, p);
  return data;
}

export async function deleteZyxel(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/zyxel/${id}`);
}

export async function testZyxel(id: string): Promise<ZyxelDiagnosis> {
  const { data } = await apiClient.post<ZyxelDiagnosis>(`/api/v1/zyxel/${id}/test`);
  return data;
}

export async function syncZyxel(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/zyxel/${id}/sync`);
  return data;
}

export async function listZyxelPolicies(id: string): Promise<ZyxelPolicy[]> {
  const { data } = await apiClient.get<{ items: ZyxelPolicy[] }>(`/api/v1/zyxel/${id}/policies`);
  return data.items ?? [];
}

export async function listZyxelAddresses(id: string): Promise<ZyxelAddressObject[]> {
  const { data } = await apiClient.get<{ items: ZyxelAddressObject[] }>(`/api/v1/zyxel/${id}/addresses`);
  return data.items ?? [];
}
