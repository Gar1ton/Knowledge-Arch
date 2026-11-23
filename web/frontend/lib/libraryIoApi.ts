/**
 * 3.0.0 外部论文入库：来源栏的入库框、文档栏的「待补 PDF」清单、文档信息栏的出处。
 * 后端：`kacore/api_library_io.py` / `web/library_io_routes.py`；补 PDF 沿用 POST /api/documents（多带 import_id）。
 */
import { apiFetch } from "./api";
import type { ExternalSourceDetail } from "./externalApi";

export type ImportStatus = "pending_pdf" | "imported" | "dismissed";

/** 入库时从账本抄下来的出处。 */
export interface ImportSource {
  conversation_id: string;
  msg_idx: number;
  question: string;
  captured_at: string;
  source_id: string;
  tool: string;
  call_label: string;
  locator: string;
  page: number | null;
}

export interface ImportRecord {
  import_id: string;
  capture_id: string;
  ord: number;
  source_id: string;
  fields: ExternalSourceDetail["fields"];
  filename: string;
  target: string;
  target_name: string;
  status: ImportStatus;
  doc_id: string;
  pdf_url: string;
  pdf_sha256: string;
  source: ImportSource;
  tried: { url: string; error: string; tool?: string }[];
  /** 找不到 PDF 时能在浏览器打开、自己下载的链接（DOI 链接、原文页、试过的链接）。 */
  links: string[];
  created_at: string;
  updated_at: string;
}

export interface ImportPreview {
  fields: ExternalSourceDetail["fields"];
  filename: string;
  /** 入库时会试几个链接（都来自这次联网补充记下的原文）。 */
  candidates: number;
  source: ImportSource;
  targets: { coll_key: string; path: string }[];
  existing: ImportRecord | null;
  in_library: { doc_id: string; title: string } | null;
}

/** 来源栏每条来源上的入库状态。 */
export interface SourceImportState {
  import_id: string;
  status: ImportStatus;
  doc_id: string;
}

export const IMPORTS_CHANGED_EVENT = "ka:imports-changed";

export function notifyImportsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(IMPORTS_CHANGED_EVENT));
}

const enc = encodeURIComponent;
// 入库要试着下载几个链接，再走一遍上传管线（与上传同样的时长上限）。
const IMPORT_TIMEOUT_MS = 300_000;

export function getImportPreview(captureId: string, ord: number): Promise<ImportPreview> {
  return apiFetch<ImportPreview>(`/api/external/captures/${enc(captureId)}/sources/${ord}/import`);
}

export function importSource(
  captureId: string,
  ord: number,
  collectionKey: string,
  filename: string,
): Promise<ImportRecord> {
  return apiFetch<ImportRecord>(`/api/external/captures/${enc(captureId)}/sources/${ord}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection_key: collectionKey, filename }),
    timeoutMs: IMPORT_TIMEOUT_MS,
  });
}

export function listImports(status?: ImportStatus): Promise<ImportRecord[]> {
  return apiFetch<ImportRecord[]>(`/api/imports${status ? `?status=${status}` : ""}`);
}

export function dismissImport(importId: string): Promise<ImportRecord> {
  return apiFetch<ImportRecord>(`/api/imports/${enc(importId)}/dismiss`, { method: "POST" });
}

/** 给待补 PDF 的记录补上用户自己下载的文件（按记录里的文件名与集合入库）。 */
export function uploadImportPdf(importId: string, file: File): Promise<ImportRecord> {
  const form = new FormData();
  form.append("file", file);
  form.append("import_id", importId);
  return apiFetch<ImportRecord>("/api/documents", {
    method: "POST",
    body: form,
    timeoutMs: IMPORT_TIMEOUT_MS,
  });
}

export function getImportOrigin(docId: string): Promise<ImportRecord | null> {
  return apiFetch<ImportRecord | null>(`/api/documents/${enc(docId)}/import-origin`);
}
