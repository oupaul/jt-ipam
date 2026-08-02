import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

// Palo Alto (PAN-OS) 防火牆整合（Beta，實驗性）。路徑帶 /api/v1 前綴（baseURL 為 /）。
// 走官方 REST API（設定物件）+ XML op API（ARP），無實機驗證過。

export interface PaloAltoFirewall {
  id: string;
  name: string;
  api_url: string;
  username: string;
  vsys: string;
  enabled: boolean;
  verify_tls: boolean;
  sync_arp: boolean;
  sync_policies: boolean;
  sync_nat: boolean;
  sync_addresses: boolean;
  sync_interval_seconds: number;
  description: string | null;
  scope_subnet_ids: string[] | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface PaloAltoWrite {
  name: string;
  api_url: string;
  username: string;
  password?: string;
  vsys?: string;
  enabled?: boolean;
  verify_tls?: boolean;
  sync_arp?: boolean;
  sync_policies?: boolean;
  sync_nat?: boolean;
  sync_addresses?: boolean;
  sync_interval_seconds?: number;
  description?: string;
  scope_subnet_ids?: string[];
}

/** 連線診斷：先換 API key，再逐端點回報是否可讀與筆數 */
export interface PaloAltoDiagnosis {
  vsys: string;
  ok_count: number;
  checks: { endpoint: string; ok: boolean; rows?: number; error?: string }[];
}

export interface PaloAltoPolicy {
  id: string; vsys: string; name: string;
  action: string | null; disabled: boolean | null;
  from_zone: string | null; to_zone: string | null;
  source: string | null; destination: string | null;
  application: string | null; service: string | null; description: string | null;
}

export interface PaloAltoAddressObject {
  id: string; vsys: string; name: string;
  obj_type: string | null; value: string | null; description: string | null;
}

export async function listPaloAlto(): Promise<Paginated<PaloAltoFirewall>> {
  const { data } = await apiClient.get<Paginated<PaloAltoFirewall>>("/api/v1/paloalto", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}

export async function createPaloAlto(p: PaloAltoWrite): Promise<PaloAltoFirewall> {
  const { data } = await apiClient.post<PaloAltoFirewall>("/api/v1/paloalto", p);
  return data;
}

export async function updatePaloAlto(id: string, p: Partial<PaloAltoWrite>): Promise<PaloAltoFirewall> {
  const { data } = await apiClient.patch<PaloAltoFirewall>(`/api/v1/paloalto/${id}`, p);
  return data;
}

export async function deletePaloAlto(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/paloalto/${id}`);
}

export async function testPaloAlto(id: string): Promise<PaloAltoDiagnosis> {
  const { data } = await apiClient.post<PaloAltoDiagnosis>(`/api/v1/paloalto/${id}/test`);
  return data;
}

export async function syncPaloAlto(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/paloalto/${id}/sync`);
  return data;
}

export async function listPaloAltoPolicies(id: string): Promise<PaloAltoPolicy[]> {
  const { data } = await apiClient.get<{ items: PaloAltoPolicy[] }>(`/api/v1/paloalto/${id}/policies`);
  return data.items ?? [];
}

export async function listPaloAltoAddresses(id: string): Promise<PaloAltoAddressObject[]> {
  const { data } = await apiClient.get<{ items: PaloAltoAddressObject[] }>(`/api/v1/paloalto/${id}/addresses`);
  return data.items ?? [];
}
