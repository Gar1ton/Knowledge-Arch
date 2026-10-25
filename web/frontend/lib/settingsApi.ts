/**
 * lib/settingsApi.ts — 设置 / 模型供应商 / 组件 / 旧数据导入的接口客户端。
 *
 * 设置项的唯一真相源是后端 SettingSpec（`GET /api/settings/schema`）：界面按 schema 渲染，
 * 写入一律 `PATCH /api/settings {key, value}`，按返回的 consequence 提示重启服务 / 重建索引。
 */

import { apiFetch } from "@/lib/api";

// ─── 设置 ──────────────────────────────────────────────────────

export type SettingKind =
  | "bool" | "int" | "float" | "str" | "text" | "enum" | "path" | "secret" | "model_ref";
export type Consequence = "none" | "restart" | "rebuild";
export type ModelRefValue = { provider_id: string; model: string };
export type SecretValue = { set: boolean; masked: string };

export interface SettingItem {
  key: string;
  kind: SettingKind;
  default: unknown;
  home: string;
  tier: "basic" | "advanced";
  consequence: Consequence;
  options: string[];
  bounds: [number, number] | null;
  label_key: string;
  owner: "backend" | "shell" | "frontend";
  nullable: boolean;
  value: unknown;
  is_set: boolean;
}

export interface SettingsSchema {
  home: string;
  flow_homes: string[];
  prefs_homes: string[];
  items: SettingItem[];
}

export interface SettingUpdateResult {
  status: string;
  key: string;
  value: unknown;
  consequence: Consequence;
  restart_required: boolean;
  rebuild_required: boolean;
}

export async function getSettingsSchema(home?: string): Promise<SettingsSchema> {
  const qs = home ? `?home=${encodeURIComponent(home)}` : "";
  return apiFetch<SettingsSchema>(`/api/settings/schema${qs}`);
}

export async function patchSetting(key: string, value: unknown): Promise<SettingUpdateResult> {
  return apiFetch<SettingUpdateResult>("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
}

/** 多项写入的合并后果：rebuild > restart > none。 */
export function worstConsequence(results: { consequence: Consequence }[]): Consequence {
  if (results.some((r) => r.consequence === "rebuild")) return "rebuild";
  if (results.some((r) => r.consequence === "restart")) return "restart";
  return "none";
}

export function isModelRef(value: unknown): value is ModelRefValue {
  return (
    typeof value === "object" && value !== null &&
    typeof (value as ModelRefValue).provider_id === "string" &&
    typeof (value as ModelRefValue).model === "string"
  );
}

export function isSecretValue(value: unknown): value is SecretValue {
  return typeof value === "object" && value !== null && "set" in value && "masked" in value;
}

// ─── 模型供应商（连接）──────────────────────────────────────────

export type ProviderKind = "openai_compatible" | "anthropic";

export interface ProviderTestResult {
  status: "ok" | "error";
  models?: number;
  code?: string;
  message?: string;
  checked_at?: number;
  elapsed_ms?: number;
}

export interface Provider {
  id: string;
  kind: ProviderKind;
  label: string;
  base_url: string;
  timeout_sec: number;
  models: string[];
  api_key_set: boolean;
  api_key_masked: string;
  status: ProviderTestResult | null;
  referenced_by: string[];
}

export interface ProviderPreset {
  id: string;
  label: string;
  kind: ProviderKind;
  base_url: string;
  needs_key: boolean;
}

export interface ProvidersList {
  providers: Provider[];
  presets: ProviderPreset[];
  kinds: ProviderKind[];
}

export type ProviderDraft = {
  label?: string;
  base_url?: string;
  kind?: ProviderKind;
  api_key?: string;
  timeout_sec?: number;
  id?: string;
};

export async function listProviders(): Promise<ProvidersList> {
  return apiFetch<ProvidersList>("/api/providers");
}

export async function createProvider(draft: ProviderDraft): Promise<Provider> {
  return apiFetch<Provider>("/api/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export async function updateProvider(
  id: string,
  draft: ProviderDraft,
): Promise<Provider & { restart_required?: boolean; rebuild_required?: boolean }> {
  return apiFetch(`/api/providers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export async function deleteProvider(id: string): Promise<void> {
  await apiFetch(`/api/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function testProvider(id: string): Promise<ProviderTestResult> {
  return apiFetch<ProviderTestResult>(`/api/providers/${encodeURIComponent(id)}/test`, {
    method: "POST",
    timeoutMs: 120_000,
  });
}

export async function refreshProviderModels(
  id: string,
): Promise<{ status: "ok" | "error"; models: string[]; code?: string; message?: string }> {
  return apiFetch(`/api/providers/${encodeURIComponent(id)}/models`, {
    method: "POST",
    timeoutMs: 120_000,
  });
}

// ─── 组件（uv extras）──────────────────────────────────────────

export interface ComponentStatus {
  key: string;
  feature: string;
  specs: string[];
  state: "installed" | "missing" | "stale";
  importable: boolean;
  active: boolean;
  installed_at: string | null;
  size_bytes: number;
}

export interface ComponentsStatus {
  installer_available: boolean;
  installer: string;
  extras_dir: string;
  components: ComponentStatus[];
}

export interface ComponentActionResult {
  status: "ok" | "error";
  key: string;
  message: string;
  restart_required: boolean;
  returncode?: number | null;
}

export async function listComponents(): Promise<ComponentsStatus> {
  return apiFetch<ComponentsStatus>("/api/components");
}

export async function installComponent(key: string): Promise<ComponentActionResult> {
  return apiFetch<ComponentActionResult>(`/api/components/${encodeURIComponent(key)}/install`, {
    method: "POST",
    timeoutMs: 1_800_000, // torch 等大包可能下载很久
  });
}

export async function uninstallComponent(key: string): Promise<ComponentActionResult> {
  return apiFetch<ComponentActionResult>(`/api/components/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}

// ─── 旧版插件数据导入 ──────────────────────────────────────────

export interface LegacyPreview {
  data_dir: string;
  config_found: boolean;
  runtime_config_found: boolean;
  document_count: number;
  file_count: number;
  size_bytes: number;
  has_database: boolean;
  has_vector_store: boolean;
  has_graph: boolean;
  has_secrets: boolean;
}

export interface LegacyImportSummary {
  document_count?: number;
  paths_rewritten?: number;
  missing_originals?: number;
  migrations_applied?: number;
  settings_translated?: number;
  providers_created?: number;
  secrets_imported?: number;
}

export interface LegacyImportJob {
  status: "running" | "restarting" | "staged" | "error";
  stage: string;
  done: number;
  total: number;
  error: string;
  summary?: LegacyImportSummary;
  attention?: string[];
  dropped?: string[];
}

export interface LegacyImportResult {
  status: "ok" | "failed";
  error?: string;
  summary: LegacyImportSummary;
  attention: string[];
  dropped: string[];
  finished_at?: string;
}

export interface LegacyImportState {
  job: LegacyImportJob | null;
  library_empty: boolean;
  pending: { import_id: string; summary: LegacyImportSummary; attention: string[] } | null;
  result: LegacyImportResult | null;
}

export async function inspectLegacyData(path: string): Promise<LegacyPreview> {
  return apiFetch<LegacyPreview>("/api/legacy-import/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function startLegacyImport(path: string): Promise<LegacyImportJob> {
  return apiFetch<LegacyImportJob>("/api/legacy-import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function getLegacyImportState(): Promise<LegacyImportState> {
  return apiFetch<LegacyImportState>("/api/legacy-import", { timeoutMs: 5_000 });
}

export async function discardLegacyImport(): Promise<void> {
  await apiFetch("/api/legacy-import", { method: "DELETE" });
}
