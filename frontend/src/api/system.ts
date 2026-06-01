import { apiClient } from "@/api/client";

export interface LLMConfig {
  enabled: boolean;
  url: string;
  embedding_model: string;
  chat_model: string;
  timeout: number;
}

export interface LLMConfigPatch {
  enabled?: boolean;
  url?: string;
  embedding_model?: string;
  chat_model?: string;
  timeout?: number;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  const { data } = await apiClient.get<LLMConfig>("/api/v1/system/llm");
  return data;
}

export async function patchLLMConfig(payload: LLMConfigPatch): Promise<LLMConfig> {
  const { data } = await apiClient.patch<LLMConfig>("/api/v1/system/llm", payload);
  return data;
}

export interface OllamaModel {
  name: string;
  size: number | null;
  modified_at: string | null;
  family: string | null;
  parameter_size: string | null;
}

export async function listOllamaModels(): Promise<{ models: OllamaModel[]; error?: string }> {
  const { data } = await apiClient.get<{ models: OllamaModel[]; error?: string }>(
    "/api/v1/system/llm/models",
  );
  return data;
}
