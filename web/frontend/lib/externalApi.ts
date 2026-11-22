/**
 * 3.0.0 联网补充：回答「来源」栏与数据流「联网补充」节点的工具面板。
 * 后端：`kacore/api_external.py`（GET /api/external/captures/{id}、/api/tools 等）。
 * 这里不认识任何具体工具：工具名、说明、设置都来自后端的工具声明。
 */
import { apiFetch } from "./api";

export interface ToolBlock {
  text: string;
  locator: string;
  page: number | null;
}

/** 账本里的一次工具调用（含 cite 本身）；label 是交给模型的编号 c1、c2…。 */
export interface ToolCallRecord {
  call_id: string;
  label: string;
  ord: number;
  tool: string;
  arguments: Record<string, unknown>;
  is_error: boolean;
  started_at: string;
  elapsed_ms: number;
  blocks: ToolBlock[];
}

/** 一条通过可追溯核验的外部引用；source_id 是来源栏编号 E1、E2…。 */
export interface ExternalSourceDetail {
  source_id: string;
  ord: number;
  fields: {
    title?: string;
    creators?: string[];
    year?: string;
    venue?: string;
    identifier?: string;
    url?: string;
  };
  passage: string;
  locator: string;
  page: number | null;
  cited: boolean;
  placement: { path?: string; new_direction?: string; reason?: string };
  call_id: string;
  call_label: string;
  tool: string;
  block: number;
}

export interface ExternalCapture {
  capture: {
    capture_id: string;
    conversation_id: string;
    msg_idx: number;
    question: string;
    actor: string;
    captured_at: string;
  };
  sources: ExternalSourceDetail[];
  calls: ToolCallRecord[];
}

export interface ToolInfo {
  name: string;
  description: string;
  input_schema: { properties?: Record<string, { type?: string }>; required?: string[] };
  enabled: boolean;
  ready: boolean;
  missing: string[];
  settings: { name: string; kind: string; required: boolean; key: string }[];
  enabled_key: string;
  last_ok_at: string;
  last_error: string;
  last_error_at: string;
  info: Record<string, string>;
}

export interface RecentToolCall extends ToolCallRecord {
  capture_id: string;
  actor: string;
  question: string;
  conversation_id: string;
}

/** 来源的作者短标签（与 Harvard 引用同规则：一人 / 两人用 and / 三人以上 et al.）。 */
export function authorsLabel(s: ExternalSourceDetail): string {
  const names = (s.fields.creators ?? []).map((n) => String(n).split(",")[0].trim()).filter(Boolean);
  if (!names.length) return "Anon.";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]} et al.`;
}

/** 调用参数的一行摘要（工具面板与来源栏用）。 */
export function argumentsLabel(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}

const enc = encodeURIComponent;

export function getExternalCapture(captureId: string): Promise<ExternalCapture> {
  return apiFetch<ExternalCapture>(`/api/external/captures/${enc(captureId)}`);
}

/** 按用户自己的集合树归类（首次打开「体系」时才调用模型；已归类的不再调用）。 */
export function placeExternalCapture(captureId: string): Promise<ExternalCapture> {
  return apiFetch<ExternalCapture>(`/api/external/captures/${enc(captureId)}/placement`, {
    method: "POST",
    timeoutMs: 120_000,
  });
}

export function exportExternalCapture(
  captureId: string,
  folder: string,
): Promise<{ files: string[]; rows: number }> {
  return apiFetch(`/api/external/captures/${enc(captureId)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder }),
  });
}

export async function listTools(): Promise<ToolInfo[]> {
  return (await apiFetch<{ tools: ToolInfo[] }>("/api/tools")).tools;
}

export async function recentToolCalls(limit = 20): Promise<RecentToolCall[]> {
  return (await apiFetch<{ calls: RecentToolCall[] }>(`/api/tools/calls?limit=${limit}`)).calls;
}

/** 工具面板的试调用：用户按「调用」才发（会发真实的外部请求），记进账本（actor=test）。 */
export function testTool(
  name: string,
  args: Record<string, unknown>,
): Promise<{ capture_id: string; call: ToolCallRecord; text: string }> {
  return apiFetch(`/api/tools/${enc(name)}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arguments: args, actor: "test", question: `${name} test`, user_approved: true }),
    timeoutMs: 120_000,
  });
}
