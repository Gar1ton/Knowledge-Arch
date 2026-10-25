import type { I18nKey, Lang } from "@/lib/i18n";
import type { PipelineStage } from "@/lib/api";

export type FlowStageStatus = PipelineStage["status"];

export type FlowStageId =
  | "zotero"
  | "ingest"
  | "embedding"
  | "vector_store"
  | "retrieval"
  | "graph"
  | "ask"
  | "models"
  | "providers"
  | "notion"
  | "r2";

export type FlowEdge = {
  from: FlowStageId;
  to: FlowStageId;
  labelKey?: I18nKey;
  dashed?: boolean;
  vertical?: boolean;
};

export type FlowStageMeta = {
  idx: number;
  titleKey: I18nKey;
  descKey: I18nKey;
  roleKey: I18nKey;
  icon: "doc" | "spark" | "db" | "layers" | "graph" | "chat" | "cloud" | "book" | "chip" | "plug" | "notion";
  kind: "pipe" | "dest" | "source";
  link?: { labelKey: I18nKey; href: string; primary?: boolean };
};

export const STAGE_META: Record<FlowStageId, FlowStageMeta> = {
  zotero: {
    idx: 0,
    titleKey: "flow_stage_zotero",
    descKey: "flow_stage_zotero_desc",
    roleKey: "flow_role_optional_source",
    icon: "book",
    kind: "source",
  },
  ingest: {
    idx: 1,
    titleKey: "flow_stage_ingest",
    descKey: "flow_stage_ingest_desc",
    roleKey: "flow_role_readonly",
    icon: "doc",
    kind: "pipe",
  },
  embedding: {
    idx: 2,
    titleKey: "flow_stage_embedding",
    descKey: "flow_stage_embedding_desc",
    roleKey: "flow_role_switchable",
    icon: "spark",
    kind: "pipe",
  },
  vector_store: {
    idx: 3,
    titleKey: "flow_stage_vector_store",
    descKey: "flow_stage_vector_store_desc",
    roleKey: "flow_role_switchable",
    icon: "db",
    kind: "pipe",
  },
  retrieval: {
    idx: 4,
    titleKey: "flow_stage_retrieval",
    descKey: "flow_stage_retrieval_desc",
    roleKey: "flow_role_default",
    icon: "layers",
    kind: "pipe",
  },
  graph: {
    idx: 5,
    titleKey: "flow_stage_graph",
    descKey: "flow_stage_graph_desc",
    roleKey: "flow_role_parallel",
    icon: "graph",
    kind: "pipe",
  },
  ask: {
    idx: 8,
    titleKey: "flow_stage_ask",
    descKey: "flow_stage_ask_desc",
    roleKey: "flow_role_interface_switchable",
    icon: "chat",
    kind: "dest",
    link: { labelKey: "flow_open_ask", href: "/ask", primary: true },
  },
  models: {
    idx: 7,
    titleKey: "flow_stage_models",
    descKey: "flow_stage_models_desc",
    roleKey: "flow_role_models",
    icon: "chip",
    kind: "pipe",
  },
  providers: {
    idx: 6,
    titleKey: "flow_stage_providers",
    descKey: "flow_stage_providers_desc",
    roleKey: "flow_role_providers",
    icon: "plug",
    kind: "source",
  },
  notion: {
    idx: 9,
    titleKey: "flow_stage_notion",
    descKey: "flow_stage_notion_desc",
    roleKey: "flow_role_interface_bypass",
    icon: "notion",
    kind: "dest",
  },
  r2: {
    idx: 10,
    titleKey: "flow_stage_r2",
    descKey: "flow_stage_r2_desc",
    roleKey: "flow_role_interface_bypass",
    icon: "cloud",
    kind: "dest",
  },
};

export const FIELD_LABEL_KEYS: Partial<Record<FlowStageId, I18nKey>> = {
  ingest: "flow_field_store",
  embedding: "flow_field_provider",
  vector_store: "flow_field_backend",
  retrieval: "flow_field_strategy",
  graph: "flow_field_enabled",
  notion: "flow_field_enabled",
  r2: "flow_field_enabled",
  models: "flow_field_chat_model",
  providers: "flow_field_connections",
};

/** 节点头部开关（Segmented）写入的设置键；toBool 时 on/off ↔ true/false。 */
export const SWITCH_MAP: Partial<Record<FlowStageId, { key: string; toBool?: boolean }>> = {
  zotero: { key: "flow.zotero.enabled", toBool: true },
  vector_store: { key: "flow.vector_store.backend" },
  graph: { key: "flow.graph.enabled", toBool: true },
  notion: { key: "flow.notion.enabled", toBool: true },
  r2: { key: "flow.r2.enabled", toBool: true },
};

/** 节点 → 设置分组（`GET /api/settings/schema?home=`）。providers 是连接列表，不在此表。 */
export const STAGE_HOME: Partial<Record<FlowStageId, string>> = {
  zotero: "flow.zotero",
  ingest: "flow.import",
  embedding: "flow.embedding",
  vector_store: "flow.vector_store",
  retrieval: "flow.retrieval",
  graph: "flow.graph",
  ask: "flow.answer",
  models: "flow.models",
  notion: "flow.notion",
  r2: "flow.r2",
};

// 主干一行（row 2）：Zotero → 导入 → 向量化 → 向量库 → 检索/图谱 → 问答。
// 旁路：R2 备份在导入上方，Notion 在问答上方；模型分配与模型供应商自下而上接入问答。
export const GRID: Record<FlowStageId, { col: number; row: number }> = {
  zotero: { col: 1, row: 2 },
  r2: { col: 2, row: 1 },
  ingest: { col: 2, row: 2 },
  embedding: { col: 3, row: 2 },
  vector_store: { col: 4, row: 2 },
  retrieval: { col: 5, row: 1 },
  graph: { col: 5, row: 3 },
  notion: { col: 6, row: 1 },
  ask: { col: 6, row: 2 },
  models: { col: 6, row: 3 },
  providers: { col: 6, row: 4 },
};

export const EDGES: FlowEdge[] = [
  { from: "zotero", to: "ingest", labelKey: "flow_edge_zotero", dashed: true },
  { from: "ingest", to: "embedding" },
  { from: "embedding", to: "vector_store" },
  { from: "vector_store", to: "retrieval", labelKey: "flow_edge_default" },
  { from: "vector_store", to: "graph", labelKey: "flow_edge_graph_mixed" },
  { from: "retrieval", to: "ask" },
  { from: "graph", to: "ask" },
  { from: "ingest", to: "r2", labelKey: "flow_edge_backup", dashed: true, vertical: true },
  { from: "ask", to: "notion", labelKey: "flow_edge_backup", dashed: true, vertical: true },
  { from: "models", to: "ask", labelKey: "flow_edge_llm", vertical: true },
  { from: "providers", to: "models", vertical: true },
];

const BACKEND_LABEL_ZH: Record<string, string> = {
  local: "本地离线",
  server: "服务器 API",
  external: "云端 API",
  milvus: "Milvus Lite",
  lexical: "SQLite 词法",
  on: "开启",
  off: "关闭",
  cross_encoder: "本地 Rerank",
  noop: "关闭 Rerank",
  idle: "待首次使用",
  loading: "加载中",
  ready: "已就绪",
  failed: "加载失败",
  rrf_fusion: "RRF",
  unassigned: "未分配",
  sqlite: "SQLite",
  sqlite_lexical: "SQLite",
  strict_mirror: "严格镜像",
  conservative: "保守同步",
  archive: "归档堆栈",
  preserve: "保留 detached",
  strict: "Strict 清理",
  managed_copy: "副本托管",
  linked: "链接 Zotero",
  zotmoov: "ZotMoov 目录",
};

const BACKEND_LABEL_EN: Record<string, string> = {
  local: "Local",
  server: "Server API",
  external: "Cloud API",
  milvus: "Milvus Lite",
  lexical: "SQLite lexical",
  on: "On",
  off: "Off",
  cross_encoder: "Local Rerank",
  noop: "Rerank Off",
  idle: "Idle",
  loading: "Loading",
  ready: "Ready",
  failed: "Failed",
  rrf_fusion: "RRF",
  unassigned: "Not assigned",
  sqlite: "SQLite",
  sqlite_lexical: "SQLite",
  strict_mirror: "Strict mirror",
  conservative: "Conservative",
  archive: "Archive",
  preserve: "Preserve detached",
  strict: "Strict cleanup",
  managed_copy: "Managed copy",
  linked: "Linked",
  zotmoov: "ZotMoov folder",
};

export function isFlowStageId(id: string): id is FlowStageId {
  return id in STAGE_META;
}

export function backendLabel(value: string, lang: Lang): string {
  return (lang === "zh" ? BACKEND_LABEL_ZH : BACKEND_LABEL_EN)[value] ?? value;
}

export function buildDetailParts(stage: PipelineStage, lang: Lang, flowEnginesLabel: string): string[] {
  const parts: string[] = [];
  const model = stage.detail.model;
  const dim = stage.detail.actual_dimension;
  const engines = stage.detail.engines;
  const pending = stage.detail.pending_reindex_count;
  const docs = stage.detail.document_count;
  const chunks = stage.detail.chunk_count;
  const rerankModel = stage.detail.rerank_model;
  const rerankStatus = stage.detail.rerank_status;

  if (typeof model === "string" && model) parts.push(model);
  if (typeof dim === "number" || typeof dim === "string") parts.push(`${dim}d`);
  if (stage.id === "retrieval" && Array.isArray(engines) && engines.length > 0) {
    parts.push(`${flowEnginesLabel} ${engines.map((e) => backendLabel(String(e), lang)).join(" + ")}`);
  }
  if (stage.id === "ask") {
    if (typeof rerankStatus === "string" && rerankStatus) {
      parts.push(lang === "zh" ? `Rerank ${backendLabel(rerankStatus, lang)}` : `Rerank ${backendLabel(rerankStatus, lang)}`);
    }
    if (typeof rerankModel === "string" && rerankModel) parts.push(rerankModel);
  }
  if (stage.id === "vector_store") {
    if (typeof docs === "number") parts.push(lang === "zh" ? `${docs} 文档` : `${docs} docs`);
    if (typeof chunks === "number") parts.push(lang === "zh" ? `${chunks} chunks` : `${chunks} chunks`);
    if (typeof pending === "number" && pending > 0) {
      parts.push(lang === "zh" ? `${pending} 待重建` : `${pending} pending`);
    }
  }
  return parts;
}
