/**
 * 3.0.0 联网补充：回答「来源」栏的数据（一次联网补充的详情、按集合归类、导出）。
 * 后端：`kacore/api_external.py`（GET /api/external/captures/{id} 等）。
 */
import { apiFetch } from "./api";

export type Trust = "verified" | "single" | "conflict" | "retracted" | "unresolved";

export interface ExternalSourceDetail {
  source_id: string;
  trust: Trust;
  meta: {
    title?: string;
    creators?: string[];
    creator_names?: string[];
    year?: string;
    venue?: string;
    item_type?: string;
    abstract?: string;
    url?: string;
    oa_pdf_url?: string;
    in_library_doc_id?: string;
    selection_reason?: string;
    [key: string]: unknown;
  };
  aliases: string[];
  placement: { path?: string; new_direction?: string; reason?: string };
  best_rank: number;
  hits: { engine: string; query: string; rank: number; is_new: boolean; request_url: string }[];
  history: { capture_id: string; rank: number }[];
  drift: [number, number] | null;
  cited: { ord: number; passage: string; page: number | null; file_sha256: string }[];
  fields: { field: string; engine: string; fetched_at: string }[];
}

export interface ExternalCapture {
  capture: {
    capture_id: string;
    conversation_id: string;
    msg_idx: number;
    question: string;
    queries: string[];
    engines: string[];
    actor: string;
    captured_at: string;
    ordinal: number;
  };
  sources: ExternalSourceDetail[];
  graph: {
    edges: { source: string; target: string; kind: "cites" | "coupling" | "cites_library"; weight: number }[];
    library_nodes: string[];
    /** 库内节点（`lib:<doc_id>`）→ 文档标题。 */
    library_titles: Record<string, string>;
  };
}

/** 来源的作者短标签（与 Harvard 引用同规则：一人 / 两人用 and / 三人以上 et al.）。 */
export function authorsLabel(s: ExternalSourceDetail): string {
  const raw = (s.meta.creators?.length ? s.meta.creators : s.meta.creator_names) ?? [];
  const names = raw.map((n) => String(n).split(",")[0].trim()).filter(Boolean);
  if (!names.length) return "Anon.";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]} et al.`;
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
