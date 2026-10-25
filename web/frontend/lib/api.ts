/**
 * lib/api.ts — 唯一网络出口
 * 所有组件禁止裸 fetch，必须经此模块。
 * 后端地址与 token 由 lib/backend.ts 提供（桌面壳 / 开发代理），每个请求带 Bearer token。
 */

import { apiUrl, authHeaders, inDesktopShell, resetBackendInfo } from "@/lib/backend";

// ─── 类型定义 ──────────────────────────────────────────────────

export interface Collection {
  name: string;
  description?: string;
  is_system?: boolean;
  origin?: "local" | "zotero";
  read_only?: boolean;
  zotero_collection_key?: string;
  coll_key?: string;
  parent_key?: string;
  library_id?: string;
}

export interface ZoteroMeta {
  item_type?: string;
  creators?: string[];
  year?: string;
  venue?: string;
  doi?: string;
  url?: string;
  abstract?: string;
}

export interface KrDocument {
  doc_id: string;
  title?: string;
  filename?: string;
  collection: string;
  tags: string[];
  size?: number;
  chunks?: number;
  updated?: string;
  ext?: string;
  needs_reindex?: boolean;
  lightrag_index_status?: { status: string; collection: string; last_error?: string } | null;
  // 制品包 / 来源 / 三指示元数据（v0.22.0）
  origin?: "local" | "zotero";
  read_only?: boolean;
  lifecycle_state?: "active" | "detached";
  library_id?: string;
  zotero_item_key?: string;
  attachment_key?: string;
  last_synced_at?: string | null;
  milvus_covered?: boolean;
  zotero_meta?: ZoteroMeta;
}

export interface EffectiveConfig {
  source_store?: Record<string, unknown>;
  r2_sync?: Record<string, unknown>;
  notion_sync?: Record<string, unknown>;
  graph?: Record<string, unknown>;
  ask?: Record<string, unknown>;
  rerank?: Record<string, unknown>;
  deep_thinking?: Record<string, unknown>;
  enhanced_recall?: Record<string, unknown>;
  vector_db?: Record<string, unknown>;
  embedding?: Record<string, unknown>;
  zotero_sync?: Record<string, unknown>;
  diagnostics?: string[];
}

export interface ZoteroConfig {
  enabled: boolean;
  access_mode: "local" | "server";
  zotero_data_dir: string;
  resolved_data_dir?: string;
  api_port: number;
  storage_mode: "managed_copy" | "linked" | "zotmoov";
  linked_root: string;
  zotmoov_root: string;
  sync_mode: "strict_mirror" | "conservative" | "archive";
  auto_sync_enabled: boolean;
  auto_sync_interval_sec: number;
  server_key_present?: boolean;
  server_key_masked?: string;
  server_user_id?: string;
  server_username?: string;
  server_access?: Record<string, unknown>;
  connection?: { connected: boolean; port: number; detail: string };
  availability?: {
    available: boolean;
    reason?: string;
    data_dir?: string;
    access_mode?: "local" | "server";
    server_user_id?: string;
    server_username?: string;
    server_access?: Record<string, unknown>;
  };
  linked_probe?: { valid: boolean; reason: string; resolved: string };
  zotmoov_probe?: { valid: boolean; reason: string; resolved: string };
}

export interface ZoteroAccountChangeRequired {
  status: "account_change_required";
  change_id: string;
  current_account: { account_id: string; account_name: string };
  new_account: { account_id: string; account_name: string };
  actions: ["replace_local", "cancel"];
}

export interface R2BackupJob {
  job_id: string;
  action: string;
  status: string;
  stage: string;
  progress: number;
  detail?: string;
  files_total: number;
  files_done: number;
  bytes_total: number;
  bytes_done: number;
  snapshot_id?: string;
  error?: string;
}

export interface R2Status {
  status: string;
  message?: string;
  bucket_used_bytes?: number;
  plugin_used_bytes?: number;
  bucket_limit_bytes?: number;
  plugin_object_count?: number;
  snapshot?: {
    snapshot_id: string;
    updated_at: string;
    logical_bytes: number;
    file_count: number;
    deduplicated_bytes: number;
  } | null;
  job?: R2BackupJob | null;
}

export interface ZoteroSyncResult {
  sync_mode?: string;
  storage_mode?: string;
  started_at?: string;
  finished_at?: string | null;
  items_mirrored?: number;
  collections_mirrored?: number;
  new?: string[];
  changed?: string[];
  removed?: string[];
  detached?: string[];
  reattached?: string[];
  skipped_unchanged?: number;
  needs_milvus_rebuild?: boolean;
  errors?: string[];
  status?: string;
  message?: string;
}

// Zotero Pull 后台同步任务快照（与 MilvusBuildJob 同构，供统一进度面板轮询）。
export interface ZoteroSyncJob {
  job_id: string;
  type?: string; // "zotero_sync"
  status: string; // running | success | partial_failure | error
  stage?: string;
  stage_label?: string;
  incremental?: boolean;
  sync_mode?: string;
  storage_mode?: string;
  access_mode?: string;
  items_total?: number;
  docs_total: number;
  docs_processed: number;
  docs_failed: number;
  skipped_unchanged?: number;
  new?: number;
  changed?: number;
  removed?: number;
  detached?: number;
  progress_percent: number;
  elapsed_seconds?: number;
  started_at?: string;
  finished_at?: string | null;
  recent_error?: string;
  errors?: string[];
}

// Notion 单向推送后台任务快照（与 ZoteroSyncJob 同构，供统一进度面板轮询）。
export interface NotionSyncJob {
  job_id: string;
  type?: string; // "notion_sync"
  status: string; // running | success | partial_failure | error
  stage?: string;
  stage_label?: string;
  force?: boolean;
  sync_mode?: string;
  docs_total: number;
  docs_processed: number;
  docs_created?: number;
  docs_updated?: number;
  docs_archived?: number;
  docs_skipped?: number;
  docs_failed?: number;
  qa_pushed?: number;
  qa_failed?: number;
  tags_pruned?: number;
  request_count?: number;
  notes_scanned?: number;
  notes_marked?: number;
  notes_failed?: number;
  model_tokens?: number;
  progress_percent: number;
  elapsed_seconds?: number;
  started_at?: string;
  finished_at?: string | null;
  recent_error?: string;
  errors?: string[];
}

export interface ZoteroProbeResult {
  connection: { connected: boolean; port?: number; detail?: string };
  read: {
    available: boolean;
    reason?: string;
    data_dir?: string;
    item_count?: number;
    collection_count?: number;
    attachment_count?: number;
    pdf_attachment_count?: number;
  };
}

export interface GraphBuildEstimate {
  collection: string;
  docs_count: number;
  chunks_count: number;
  chars_count: number;
  estimated_lrag_chunks?: number;
  estimated_llm_calls_min: number;
  estimated_llm_calls_max: number;
  estimated_embedding_batches: number;
  estimated_duration_seconds_min: number;
  estimated_duration_seconds_max: number;
  seconds_per_chunk?: number;
  runtime_profile?: "local" | "remote" | string;
  estimate_notice: string;
}

export interface GraphBuildJob {
  job_id: string;
  collection: string;
  engine: "lightrag_core";
  status: string;
  stage?: string;
  paused?: boolean;
  pause_requested?: boolean;
  pause_message?: string;
  processed_docs?: number;
  failed_docs?: number;
  total_docs?: number;
  processed_chunks?: number;
  failed_chunks?: number;
  total_chunks?: number;
  current_doc_id?: string;
  current_chunk_index?: number;
  progress_basis?: "lrag_chunks" | "estimated_lrag_chunks" | string;
  progress_current?: number;
  progress_total?: number;
  progress_percent?: number;
  progress_label?: string;
  elapsed_seconds?: number;
  average_seconds_per_chunk?: number | null;
  estimated_remaining_seconds?: number | null;
  paused_seconds?: number;
  paused_at?: string | null;
  started_at?: string;
  finished_at?: string | null;
  recent_error?: string;
}

export interface AskSource {
  n: number;
  doc_id: string;
  title: string;
  chunk_id: string;
  ordinal: number;
  text: string;
  rrf_score?: number;
  // v1.1.0 书目字段：Zotero 与本地文档一视同仁（此前只有 Zotero 文档能拿到）。
  creators?: string[];
  author?: string;
  year?: string;
  venue?: string;
  doi?: string;
  url?: string;
  pages?: number[];
  // 后端渲染好的 Harvard 串。harvard_in_text 是正文里出现的**内层**串（不含括号），
  // 前端据它还原「点击引用 → 跳来源卡片」。
  harvard_in_text?: string;
  harvard_reference?: string;
}

export interface ThinkingChecklistItem {
  id: string;
  text: string;
  critical: boolean;
  satisfied: boolean;
  origin?: string;
}

export interface ThinkingRound {
  round: number;
  queries: string[];
  gaps: string[];
  discovered?: string[];
  kept_chunk_ids: string[];
  llm_calls: number;
  est_tokens: number;
}

export interface ThinkingTrace {
  degraded: boolean;
  degraded_reason?: string;
  verified: boolean;
  verify_missing: string[]; // 硬缺失（臆造/矛盾/错配/关键项未满足）
  verify_notes?: string[]; // 软项（部分支持·有据推断 + 信息缺口）
  est_total_tokens: number;
  checklist: ThinkingChecklistItem[];
  rounds: ThinkingRound[];
}

export interface AskResult {
  conversation_id: string;
  answer: string;
  // 证据不足/未验证提示（结构化字段，v0.30.0 起不再拼在正文开头）。
  answer_notice?: string;
  // Harvard 参考文献表（已去重排序）。答案正文尾部已含同一份，此字段供非 Web 调用方使用。
  references?: string[];
  sources: AskSource[];
  requested_retrieval_mode:
    | "default"
    | "enhanced"
    | "graph_mixed"
    | "graph_only"
    | "fulltext"
    | "deep_thinking";
  actual_retrieval_mode: string;
  retrieval_engines: string[];
  fallback_reason?: string | null;
  thinking_trace?: ThinkingTrace | null;
}

/**
 * `/api/ask` 与文档分页读在正文超阈值时返回的 409 体（`status:
 * "fulltext_confirmation_required"`）。
 *
 * 不是失败而是一次询问：把这些数字展示给用户，取得同意后带 `confirmed: true` 重发同一请求。
 * 阈值由后端回传，前端**不复制**这个数字——阈值只有一个真相源（kacore/retrieval_modes.py）。
 */
export interface FullTextConfirmation {
  status: "fulltext_confirmation_required";
  message: string;
  doc_id: string;
  title: string;
  total_chars: number;
  requested_chars: number;
  threshold_chars: number;
  estimated_tokens: number;
}

export interface ReservedResult {
  reserved: true;
  available_in: string;
}

export type MaybeReserved<T> = T | ReservedResult;

// ─── 错误类 ────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: Record<string, unknown>,
    /** 客户端主动放弃等待（后端很可能还在跑）：调用方据此区分「超时」与「真失败」。 */
    public readonly timedOut: boolean = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 后端 ask.task_timeout_seconds 到点（HTTP 202 status="ask_task_timeout"）：请求没有失败，
 * 只是后端放弃了「等它跑完再回」，任务仍在后台继续运行、完成后会正常写入 chat_history。
 * 与 ApiError.timedOut（客户端主动放弃等待）语义相近但成因不同，调用方通常一并处理
 * （见 ChatPanel 的补轮询逻辑）。
 */
export class AskTaskTimeoutError extends Error {
  constructor(
    public readonly conversationId: string,
    public readonly timeoutSeconds: number,
  ) {
    super(`The ask task exceeded the backend timeout of ${timeoutSeconds}s and is still running in the background`);
    this.name = "AskTaskTimeoutError";
  }
}

// ─── 核心 fetch 封装 ───────────────────────────────────────────

const DEFAULT_API_TIMEOUT_MS = 30_000;

// 长任务专用时限：默认 30s 会把「正常但耗时」的请求误报成超时——后端其实还在跑，
// 用户却看到「请求超时，请稍后重试」，重试又叠一份负载。各值按最坏情况给足余量，
// 但仍保留上限，避免请求永远悬着（timeoutMs: 0 = 不设限，留给调用方显式选择）。
const ASK_TIMEOUT_MS = 600_000; // 深度思考 / 图谱问答可跑数分钟
const UPLOAD_TIMEOUT_MS = 300_000; // PDF 清洗 + 切块 + 首次索引
const DEPENDENCY_INSTALL_TIMEOUT_MS = 900_000; // pip 安装 torch 等大包

export type ApiFetchOptions = RequestInit & {
  timeoutMs?: number;
};

export async function apiFetch<T>(
  path: string,
  options?: ApiFetchOptions,
): Promise<T> {
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal, ...fetchOptions } = options ?? {};
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timedOut = false;
  let timeout: ReturnType<typeof setTimeout> | null = null;
  let abortFromCaller: (() => void) | null = null;

  if (controller && signal) {
    abortFromCaller = () => controller.abort();
    if (signal.aborted) abortFromCaller();
    else signal.addEventListener("abort", abortFromCaller, { once: true });
  }
  if (controller && timeoutMs > 0) {
    timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  let res: Response;
  try {
    const headers = new Headers(fetchOptions.headers);
    for (const [name, value] of Object.entries(await authHeaders())) headers.set(name, value);
    res = await fetch(await apiUrl(path), {
      ...fetchOptions,
      headers,
      signal: controller?.signal ?? signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new ApiError(
        0,
        timedOut ? "The request timed out; please try again later" : "The request was cancelled",
        undefined,
        timedOut,
      );
    }
    // 桌面壳里网络错误多半是后端重启换了端口：丢弃缓存，下一次请求重新向壳要地址。
    if (inDesktopShell()) resetBackendInfo();
    throw err;
  } finally {
    if (timeout) clearTimeout(timeout);
    if (signal && abortFromCaller) signal.removeEventListener("abort", abortFromCaller);
  }
  if (!res.ok) {
    let msg = res.statusText;
    let parsedBody: Record<string, unknown> | undefined;
    try {
      const body = await res.json() as Record<string, unknown>;
      parsedBody = body;
      if (res.status === 501 && body.status === "reserved") {
        return {
          ...body,
          reserved: true,
        } as T;
      }
      msg = String(body.error || body.message || body.detail || msg);
    } catch {
      /* ignore parse error */
    }
    throw new ApiError(res.status, msg, parsedBody);
  }
  return res.json() as Promise<T>;
}

// ─────────────────────────────────────────────────────────────
// 集合 Collections
// ─────────────────────────────────────────────────────────────

export async function listCollections(): Promise<Collection[]> {
  return apiFetch<Collection[]>("/api/collections");
}

export async function createCollection(
  name: string,
  description?: string,
  parentKey?: string
): Promise<Collection> {
  return apiFetch<Collection>("/api/collections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, parent_key: parentKey ?? "" }),
  });
}

export async function deleteCollection(name: string): Promise<void> {
  await apiFetch<{ status: string }>(`/api/collections/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export async function renameCollection(collKey: string, name: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/collections/by-key/${encodeURIComponent(collKey)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteCollectionByKey(collKey: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/collections/by-key/${encodeURIComponent(collKey)}`, {
    method: "DELETE",
  });
}

// ─────────────────────────────────────────────────────────────
// 文档 Documents
// ─────────────────────────────────────────────────────────────

export async function listDocuments(opts?: {
  collection?: string;
  tag?: string;
}): Promise<KrDocument[]> {
  const params = new URLSearchParams();
  if (opts?.collection) params.set("collection", opts.collection);
  if (opts?.tag) params.set("tag", opts.tag);
  const qs = params.toString();
  return apiFetch<KrDocument[]>(`/api/documents${qs ? `?${qs}` : ""}`);
}

export async function uploadDocument(
  file: File,
  collection: string = "default",
  tags: string[] = []
): Promise<KrDocument> {
  const form = new FormData();
  form.append("file", file);
  form.append("collection", collection);
  if (tags.length) form.append("tags", tags.join(","));
  return apiFetch<KrDocument>("/api/documents", {
    method: "POST",
    body: form,
    timeoutMs: UPLOAD_TIMEOUT_MS,
  });
}

export async function patchDocument(
  id: string,
  patch: { collection?: string; tags?: string[] }
): Promise<KrDocument> {
  return apiFetch<KrDocument>(`/api/documents/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function updateDocumentMeta(
  docId: string,
  meta: Partial<ZoteroMeta> & { title?: string },
): Promise<KrDocument> {
  return apiFetch<KrDocument>(`/api/documents/${encodeURIComponent(docId)}/meta`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(meta),
  });
}

export async function reextractDocument(id: string): Promise<{ chunk_count: number; converter_version: string }> {
  return apiFetch(`/api/documents/${encodeURIComponent(id)}/reextract`, { method: "POST" });
}

// ─────────────────────────────────────────────────────────────
// 配置 Config
// ─────────────────────────────────────────────────────────────

export async function getEffectiveConfig(timeoutMs = 8_000): Promise<EffectiveConfig> {
  return apiFetch<EffectiveConfig>("/api/config/effective", { timeoutMs });
}

export interface ServiceRestartResult {
  status: string;
  message: string;
}

/** 触发服务软重启（teardown + 重新 initialize，重读持久化设置）；进程与端口不变，期间请求短暂返回 503。 */
export async function restartService(): Promise<ServiceRestartResult> {
  return apiFetch<ServiceRestartResult>("/api/service/restart", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export interface RebuildIndexResult {
  status?: string;
  rebuilt_docs?: number;
  rebuilt_chunks?: number;
  failed_docs?: number;
  errors?: Array<{ doc_id: string; error: string }>;
  message?: string;
  job?: MilvusBuildJob | null;
}

// Milvus 向量库后台重建任务快照（参考 LightRAG GraphBuildJob，无暂停）。
export interface MilvusBuildJob {
  job_id: string;
  type?: string;
  mode?: string;
  status: string; // running | success | partial_failure | error
  stage?: string; // data_cleaning | vector_indexing | finalizing
  stage_label?: string;
  total_docs: number;
  processed_docs: number;
  failed_docs: number;
  total_clean_docs?: number;
  processed_clean_docs?: number;
  total_index_docs?: number;
  processed_index_docs?: number;
  total_chunks: number;
  progress_percent: number;
  elapsed_seconds?: number;
  started_at?: string;
  finished_at?: string | null;
  recent_error?: string;
  errors?: Array<{ doc_id: string; stage?: string; error: string }>;
}

// 触发后台重建：立即返回 { status: "started", job }，进度交给 getActiveMilvusBuildJob 轮询。
export async function rebuildIndexPending(): Promise<RebuildIndexResult> {
  return apiFetch("/api/documents/rebuild-index", { method: "POST" });
}

export async function getActiveMilvusBuildJob(): Promise<MilvusBuildJob | null> {
  const res = await apiFetch<{ job: MilvusBuildJob | null }>("/api/documents/rebuild-index/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

// 文档上传/摄入任务快照（供统一进度面板，parsing → indexing）。
export interface IngestJob {
  job_id: string;
  type?: string; // "ingest"
  status: string; // running | success | error
  stage?: string;
  stage_label?: string;
  title?: string;
  doc_id?: string;
  progress_percent: number;
  elapsed_seconds?: number;
  started_at?: string;
  finished_at?: string | null;
  recent_error?: string;
}

export async function getActiveIngestJob(): Promise<IngestJob | null> {
  const res = await apiFetch<{ job: IngestJob | null }>("/api/documents/ingest/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

export interface ChatMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources: AskSource[];
  retrieval_mode: string;
  created_at: string;
  locked?: boolean;
  locked_at?: string | null;
  updated_at?: string | null;
}

export async function getChatHistory(conversationId: string): Promise<ChatMessage[]> {
  const res = await apiFetch<{ messages: ChatMessage[] }>(
    `/api/chat/history?conversation_id=${encodeURIComponent(conversationId)}`
  );
  return res.messages;
}

export async function clearChatHistory(
  conversationId: string,
  preserveLocked = false,
): Promise<void> {
  const qs = new URLSearchParams({ conversation_id: conversationId });
  if (preserveLocked) qs.set("preserve_locked", "true");
  await apiFetch<{ status: string }>(
    `/api/chat/history?${qs}`,
    { method: "DELETE" }
  );
}

// ─────────────────────────────────────────────────────────────
// 知识图谱 Graph
// ─────────────────────────────────────────────────────────────

export async function estimateGraphBuild(collection?: string): Promise<GraphBuildEstimate> {
  return apiFetch<GraphBuildEstimate>("/api/graph/build/estimate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collection ? { collection } : {}),
  });
}

export async function buildGraph(collection?: string): Promise<GraphBuildJob> {
  return apiFetch<GraphBuildJob>("/api/graph/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection, confirmed: true }),
  });
}

export async function getActiveBuildJob(): Promise<GraphBuildJob | null> {
  const res = await apiFetch<{ job: GraphBuildJob | null }>("/api/graph/build/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

export interface BuildJobRecord {
  job_id: string;
  collection: string;
  status: string;
  stage: string;
  processed_docs: number;
  failed_docs: number;
  total_docs: number;
  processed_chunks: number;
  failed_chunks?: number;
  total_chunks: number;
  recent_error: string;
  started_at: string;
  finished_at: string | null;
  created_at: string;
  pause_requested?: boolean;
  paused_at?: string | null;
  paused_seconds?: number;
  progress_current?: number;
  progress_total?: number;
}

export async function getBuildJobHistory(collection?: string): Promise<BuildJobRecord[]> {
  const qs = collection ? `?collection=${encodeURIComponent(collection)}` : "";
  const res = await apiFetch<{ jobs: BuildJobRecord[] }>(`/api/graph/build/history${qs}`);
  return res.jobs;
}

export async function pauseBuildJob(jobId: string): Promise<void> {
  await apiFetch(`/api/graph/build/${encodeURIComponent(jobId)}/pause`, { method: "POST" });
}

export async function resumeBuildJob(jobId: string): Promise<void> {
  await apiFetch(`/api/graph/build/${encodeURIComponent(jobId)}/resume`, { method: "POST" });
}

export interface CancelBuildResult {
  job_id: string;
  status: string;
  cleanup: {
    workspace: boolean;
    index_status_rows: number;
    codex_tasks: number;
    active_state_removed: boolean;
  };
}

export async function cancelBuildJob(jobId: string, cleanup = true): Promise<CancelBuildResult> {
  return apiFetch<CancelBuildResult>(`/api/graph/build/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cleanup }),
  });
}

// ─────────────────────────────────────────────────────────────
// 同步 / 备份 / Notion
// ─────────────────────────────────────────────────────────────

export async function notionInit(
  parentPageId: string,
  databaseTitle: string
): Promise<{ status: string; database_id?: string; qa_database_id?: string; message?: string }> {
  return apiFetch("/api/notion/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_page_id: parentPageId, database_title: databaseTitle }),
  });
}

export interface NotionPushStatus {
  status: string;
  database_id: string;
  qa_database_id: string;
  data_source_id: string;
  qa_data_source_id: string;
  documents: Record<string, number>;
  outbox_pending: number;
  outbox_failed: number;
  errors: { message: string; count: number }[];
}

export async function getNotionPushStatus(): Promise<NotionPushStatus> {
  return apiFetch("/api/sync/notion/status");
}

export async function pushNotionNote(payload: {
  content: string;
  title?: string;
  tags?: string[];
  citations?: string[];
  source?: string;
  keep_local?: boolean;
}): Promise<{ status: string; page_id?: string; message?: string }> {
  return apiFetch("/api/notion/push-note", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// 统一进度面板轮询：当前 running/partial/error 的 Notion 推送任务（无任务 → null）。
export async function getActiveNotionSyncJob(): Promise<NotionSyncJob | null> {
  const res = await apiFetch<{ job: NotionSyncJob | null }>("/api/sync/notion/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

export async function syncDocuments(
  target: "r2" | "notion" | "all",
  docIds?: string[]
): Promise<MaybeReserved<{ status: string; synced_count?: number; failed_count?: number }>> {
  return apiFetch(`/api/sync/${target}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(docIds ? { doc_ids: docIds } : {}),
  });
}

// ─────────────────────────────────────────────────────────────
// Zotero 单向 Pull 同步
// ─────────────────────────────────────────────────────────────

export async function getZoteroConfig(timeoutMs = 8_000): Promise<ZoteroConfig> {
  return apiFetch<ZoteroConfig>("/api/zotero/config", { timeoutMs });
}

export async function saveZoteroServerKey(apiKey: string): Promise<ZoteroConfig | ZoteroAccountChangeRequired> {
  return apiFetch<ZoteroConfig | ZoteroAccountChangeRequired>("/api/zotero/server-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export async function resolveZoteroAccountChange(
  changeId: string,
  action: "replace_local" | "cancel",
): Promise<{ status: string; config?: ZoteroConfig; sync?: ZoteroSyncResult }> {
  return apiFetch("/api/zotero/account-change", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: changeId, action }),
  });
}

export async function deleteZoteroServerKey(): Promise<ZoteroConfig> {
  return apiFetch<ZoteroConfig>("/api/zotero/server-key", { method: "DELETE" });
}

export async function syncZoteroPull(incremental = true): Promise<ZoteroSyncResult> {
  return apiFetch<ZoteroSyncResult>("/api/sync/zotero/pull", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incremental }),
    timeoutMs: 5_000,
  });
}

export async function getZoteroSyncStatus(): Promise<ZoteroSyncResult> {
  return apiFetch<ZoteroSyncResult>("/api/sync/zotero/status", { timeoutMs: 4_000 });
}

// 统一进度面板轮询：当前 running/partial/error 的同步任务（success/无任务 → null）。
export async function getActiveZoteroSyncJob(): Promise<ZoteroSyncJob | null> {
  const res = await apiFetch<{ job: ZoteroSyncJob | null }>("/api/sync/zotero/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

// 本地离线探针：连接测试 + zotero.sqlite 干读计数（不写库、不同步）。
export async function probeZoteroLocal(): Promise<ZoteroProbeResult> {
  return apiFetch<ZoteroProbeResult>("/api/zotero/probe");
}

export async function backupNow(force = false): Promise<MaybeReserved<unknown>> {
  return apiFetch("/api/backup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}

export async function restoreBackup(autoRestart = true): Promise<MaybeReserved<unknown>> {
  return apiFetch("/api/restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_restart: autoRestart }),
  });
}

export async function getR2Status(): Promise<R2Status> {
  return apiFetch<R2Status>("/api/r2/status", { timeoutMs: 15_000 });
}

export async function getR2Job(): Promise<R2BackupJob | null> {
  const result = await apiFetch<{ job: R2BackupJob | null }>("/api/r2/job");
  return result.job;
}

// ─────────────────────────────────────────────────────────────
// Ask Agent
// ─────────────────────────────────────────────────────────────

export async function ask(opts: {
  question: string;
  collection?: string | null;
  doc_id?: string | null;
  top_k?: number;
  conversation_id?: string | null;
  persona_enabled?: boolean;
  retrieval_mode?:
    | "default"
    | "enhanced"
    | "graph_mixed"
    | "graph_only"
    | "fulltext"
    | "deep_thinking";
  use_english_retrieval?: boolean;
  answer_language?: "auto" | "zh" | "en";
  /** 全文检索确认门：正文超阈值时后端返回 409，用户同意后带 true 重发同一请求。 */
  confirmed?: boolean;
}): Promise<AskResult> {
  const result = await apiFetch<AskResult | AskTaskTimeoutBody>("/api/ask", {
    method: "POST",
    timeoutMs: ASK_TIMEOUT_MS,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: opts.question,
      collection: opts.collection ?? null,
      doc_id: opts.doc_id ?? null,
      top_k: opts.top_k ?? 6,
      conversation_id: opts.conversation_id ?? null,
      persona_enabled: opts.persona_enabled ?? false,
      retrieval_mode: opts.retrieval_mode ?? "default",
      use_english_retrieval: opts.use_english_retrieval ?? false,
      answer_language: opts.answer_language ?? "auto",
      confirmed: opts.confirmed ?? false,
    }),
  });
  // HTTP 202：后端 task_timeout_seconds 到点，任务仍在后台跑——不是失败，但也不是
  // 正常结果，转成一个专门的异常，调用方（ChatPanel）据此转入补轮询而不是当作答案渲染。
  if ((result as AskTaskTimeoutBody).status === "ask_task_timeout") {
    const body = result as AskTaskTimeoutBody;
    throw new AskTaskTimeoutError(body.conversation_id, body.timeout_seconds);
  }
  return result as AskResult;
}

interface AskTaskTimeoutBody {
  status: "ask_task_timeout";
  message: string;
  conversation_id: string;
  timeout_seconds: number;
}

// deep thinking 实时进度：检索/校验进行中轮询时携带的逐轮增量 trace（与最终 thinking_trace 同形）。
export interface LiveProgressDetail {
  phase: string; // "plan" | "round" | "finalize"
  checklist: ThinkingChecklistItem[];
  rounds: ThinkingRound[];
}

export interface AskProgress {
  stage: string;
  pct: number;
  detail?: LiveProgressDetail;
}

/** 轮询单次 ask 进度；无进度（404）或网络抖动时返回 null，不打断调用方的轮询循环。 */
export async function getAskProgress(conversationId: string): Promise<AskProgress | null> {
  try {
    return await apiFetch<AskProgress>(
      `/api/ask/progress/${encodeURIComponent(conversationId)}`
    );
  } catch {
    return null;
  }
}

/**
 * 客户端超时（ApiError.timedOut）或后端 task_timeout_seconds 到点（AskTaskTimeoutError）后
 * 的补轮询：任务并未被取消，仍在后台跑，跑完会正常写入 chat_history——这里持续轮询
 * /api/ask/progress 直到看到 stage="done"（或进度记录已过期/不存在，说明早就跑完了），
 * 再拉一次 chat_history 找回迟到的答案。找不到时返回 null，调用方兜底提示手动刷新。
 */
export async function waitForLateAskAnswer(
  conversationId: string,
  opts?: { maxWaitMs?: number; intervalMs?: number },
): Promise<ChatMessage[] | null> {
  const maxWaitMs = opts?.maxWaitMs ?? 15 * 60_000;
  const intervalMs = opts?.intervalMs ?? 3_000;
  const deadline = Date.now() + maxWaitMs;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const progress = await getAskProgress(conversationId);
    if (progress !== null && progress.stage !== "done") continue; // 仍在跑，继续等
    // stage="done" 或进度记录已不存在（TTL 过期，大概率早已跑完）：去历史里找答案。
    try {
      const history = await getChatHistory(conversationId);
      if (history.some((m) => m.role === "assistant")) return history;
    } catch {
      /* 网络抖动，下一轮再试 */
    }
    if (progress === null) return null; // 没有进度可依据、历史里也没有答案：放弃轮询
  }
  return null;
}

// ─────────────────────────────────────────────────────────────
// 图谱统计
// ─────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────
// 系统信息 & 本地模型管理
// ─────────────────────────────────────────────────────────────

export interface SystemInfo {
  cwd: string; data_dir: string; db_file: string; docs_dir: string;
  python_version: string; platform: string;
}
export interface LocalModel { name: string; dir_name: string; size_bytes: number; last_modified: string | null; }

export async function getSystemInfo(): Promise<SystemInfo> {
  return apiFetch<SystemInfo>("/api/system/info");
}

export interface FsBrowseResult {
  path: string;
  parent: string | null;
  dirs: string[];
}

export async function browseDir(path?: string): Promise<FsBrowseResult> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiFetch<FsBrowseResult>(`/api/fs/browse${qs}`);
}

export async function listLocalModels(): Promise<LocalModel[]> {
  return apiFetch<LocalModel[]>("/api/models/local");
}

export async function deleteLocalModel(name: string): Promise<void> {
  await apiFetch<{ deleted: string }>(`/api/models/local/${encodeURIComponent(name.replace("/", "--"))}`, { method: "DELETE" });
}

// ─────────────────────────────────────────────────────────────
// 日志流
// ─────────────────────────────────────────────────────────────

export interface LogLine {
  seq: number; ts: number; level: string; name: string; msg: string;
  location?: string; exc?: string;
  source?: string; category?: string; operation?: string; status?: string;
  elapsed_ms?: number | null; metadata?: Record<string, unknown>;
}
export interface LogsResponse {
  lines: LogLine[]; server_ts: number;
  oldest_seq: number; latest_seq: number; dropped_count: number;
}

export async function postLogEvent(event: {
  type: "info" | "error" | "ok";
  message: string;
  route?: string;
}): Promise<void> {
  await apiFetch<{ status: string }>("/api/logs/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });
}

export async function getLogs(afterSeq = 0, limit = 200): Promise<LogsResponse> {
  return apiFetch<LogsResponse>(`/api/logs?after_seq=${afterSeq}&limit=${limit}`, { timeoutMs: 4_000 });
}

// ─────────────────────────────────────────────────────────────
// 系统能力 / 数据流 / 可选依赖（向导页 + 依赖管理面板）
// 后端唯一真相源：core/capabilities.py。前端只渲染，不再反推状态。
// ─────────────────────────────────────────────────────────────

/** 数据流单个环节的结构化快照。status: ready|degraded|off|info。consequence: none|restart|rebuild。 */
export interface PipelineStage {
  id: string;
  current: string;
  candidates: string[];
  status: "ready" | "degraded" | "off" | "info";
  switchable: boolean;
  consequence: "none" | "restart" | "rebuild";
  required_deps: string[];
  configured: boolean;
  detail: Record<string, unknown>;
}

/** 单个可选依赖的安装状态。 */
export interface DependencyStatus {
  key: string;
  import_name: string;
  dist_name: string;
  pip_spec: string;
  feature: string;
  stages: string[];
  required?: boolean;
  installed: boolean;
  /** 顶层包装了 ≠ 功能能跑（如 pymilvus 装了但缺 milvus-lite）。缺省视为与 installed 相同。 */
  runtime_ready?: boolean;
  /** runtime_ready=false 时的中文修复建议，直接展示给用户。 */
  runtime_hint?: string;
  version: string | null;
}

export interface CapabilitiesData {
  pipeline: PipelineStage[];
  dependencies: DependencyStatus[];
  diagnostics: string[];
}

export interface InstallResult {
  status: "ok" | "error";
  package?: string;
  returncode?: number;
  restart_required?: boolean;
  message?: string;
  reason?: import("./settingsApi").InstallFailureReason;
  detail?: string[];
}

export async function getCapabilities(): Promise<CapabilitiesData> {
  // 8s 而非 1.5s：该端点会聚合 SQLite 统计与 Milvus 运行态，重建/加载模型时 1.5s 必超，
  // 于是数据流页会毫无征兆地弹「请求超时」。
  return apiFetch<CapabilitiesData>("/api/capabilities", { timeoutMs: 8_000 });
}

// ── 本地模型驻留 / 显存 ────────────────────────────────────────

export type ModelRuntimeState = "idle" | "loading" | "ready" | "failed" | "external";

export interface AcceleratorInfo {
  device: string;
  total_bytes: number;
  free_bytes: number;
  used_bytes: number;
  allocated_bytes: number;
  reserved_bytes: number;
  torch_version: string;
}

export interface ModelRuntimeEntry {
  kind: "embedding" | "rerank";
  provider: string;
  state: ModelRuntimeState;
  model: string | null;
  device?: string;
  enabled?: boolean;
  idle_timeout_seconds?: number;
  last_error?: string | null;
}

export interface ModelRuntime {
  /** 无 torch / 无 CUDA 时为 null——面板据此降级为「CPU 模式」，不是错误。 */
  accelerator: AcceleratorInfo | null;
  models: ModelRuntimeEntry[];
  resident_count: number;
  unloaded?: string[];
  errors?: Record<string, string>;
}

export async function getModelRuntime(): Promise<ModelRuntime> {
  return apiFetch<ModelRuntime>("/api/system/models", { timeoutMs: 8_000 });
}

export async function unloadModels(kinds?: string[]): Promise<ModelRuntime> {
  return apiFetch<ModelRuntime>("/api/system/models/unload", {
    method: "POST",
    // 卸载要等 CUDA 缓存回收，比普通 GET 慢；沿用能力探测那档超时。
    timeoutMs: 8_000,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kinds: kinds ?? [] }),
  });
}

/** 安装失败时后端用 500 返回结构化结果（含失败归类）：当作结果交给调用方，而不是抛异常。 */
export async function installResultOf<T extends { status: string }>(request: Promise<T>): Promise<T> {
  try {
    return await request;
  } catch (err) {
    if (err instanceof ApiError && err.body?.status === "error") return err.body as unknown as T;
    throw err;
  }
}

export async function installDependency(pkg: string): Promise<InstallResult> {
  return installResultOf(apiFetch<InstallResult>("/api/dependencies/install", {
    method: "POST",
    timeoutMs: DEPENDENCY_INSTALL_TIMEOUT_MS,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ package: pkg }),
  }));
}

export async function recheckDependencies(): Promise<CapabilitiesData> {
  return apiFetch<CapabilitiesData>("/api/dependencies/recheck", {
    method: "POST",
    timeoutMs: 10_000,
  });
}

// ─────────────────────────────────────────────────────────────
// v0.23.0 新 UI 后端能力桩（部分端口尚未实现，501 降级）
// ─────────────────────────────────────────────────────────────

export interface DocumentNote {
  id: string;
  scope_type?: "document" | "collection";
  scope_key?: string;
  doc_id?: string;
  collection_name?: string;
  content: string;
  body?: string;
  note_html?: string;
  linked?: boolean;
  source?: string;
  library_id?: string;
  parent_item_key?: string;
  parent_attachment_key?: string;
  zotero_note_key?: string;
  zotero_version?: number;
  tags?: string[];
  collections?: string[];
  raw_zotero_json?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ConsoleScopeState {
  scope_type: string;
  scope_key: string;
  selected_collection: string;
  selected_doc_id: string;
  note_doc_id: string;
  right_panel?: string;
  reading_mode?: string;
  payload?: Record<string, unknown>;
  updated_at?: string | null;
}

export interface ZoteroAnnotation {
  id: string;
  doc_id: string;
  text: string;
  comment?: string;
  color?: string;
  page?: number;
  page_label?: string;
  type?: string;
  position?: { pageIndex?: number; rects?: number[][] };
  created_at?: string;
  updated_at?: string;
}

export interface DocumentChunk {
  chunk_id: string;
  doc_id?: string;
  ordinal: number;
  page?: number;
  text: string;
  metadata?: Record<string, unknown>;
}

export async function getDocumentNotes(docId: string): Promise<DocumentNote[]> {
  return apiFetch<DocumentNote[]>(`/api/documents/${encodeURIComponent(docId)}/notes`);
}

export async function createDocumentNote(
  docId: string,
  content: string,
  options?: { linked?: boolean; source?: string; chat_conversation_id?: string; chat_message_id?: number },
): Promise<DocumentNote> {
  return apiFetch<DocumentNote>(`/api/documents/${encodeURIComponent(docId)}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, ...(options ?? {}) }),
  });
}

export async function createCollectionNote(
  collectionName: string,
  content: string,
  options?: { linked?: boolean; source?: string; chat_conversation_id?: string; chat_message_id?: number },
): Promise<DocumentNote> {
  return apiFetch<DocumentNote>(`/api/collections/${encodeURIComponent(collectionName)}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, ...(options ?? {}) }),
  });
}

export async function getDocumentAnnotations(docId: string): Promise<ZoteroAnnotation[]> {
  return apiFetch<ZoteroAnnotation[]>(
    `/api/documents/${encodeURIComponent(docId)}/annotations`,
  );
}

export async function lockChatAnswer(
  convId: string,
  msgIdx: number,
  locked = true,
): Promise<ChatMessage> {
  return apiFetch<ChatMessage>(`/api/chat/history/${encodeURIComponent(convId)}/messages/${msgIdx}/lock`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locked }),
  });
}

export async function getConsoleScopeState(
  scopeType: string,
  scopeKey: string,
): Promise<ConsoleScopeState | null> {
  const qs = new URLSearchParams({ scope_type: scopeType, scope_key: scopeKey });
  try {
    return await apiFetch<ConsoleScopeState>(`/api/console/scope-state?${qs}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function upsertConsoleScopeState(
  state: Omit<ConsoleScopeState, "updated_at">,
): Promise<ConsoleScopeState> {
  return apiFetch<ConsoleScopeState>("/api/console/scope-state", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  });
}

export async function listDocumentChunks(docId: string): Promise<DocumentChunk[]> {
  return apiFetch<DocumentChunk[]>(
    `/api/documents/${encodeURIComponent(docId)}/chunks`,
  );
}
