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
  | "r2"
  | "external";

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
  icon: "doc" | "spark" | "db" | "layers" | "graph" | "chat" | "cloud" | "book" | "chip" | "plug" | "notion" | "globe";
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
    roleKey: "flow_role_builtin",
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
  // 3.0.0 联网补充：回答时引用外部文献；只在对话栏打开开关时运行。
  external: {
    idx: 11,
    titleKey: "flow_stage_external",
    descKey: "flow_stage_external_desc",
    roleKey: "flow_role_optional_source",
    icon: "globe",
    kind: "source",
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
  external: "flow_field_enabled",
  models: "flow_field_chat_model",
  providers: "flow_field_connections",
};

/** 节点头部开关（Segmented）写入的设置键；toBool 时 on/off ↔ true/false。 */
export const SWITCH_MAP: Partial<Record<FlowStageId, { key: string; toBool?: boolean }>> = {
  zotero: { key: "flow.zotero.enabled", toBool: true },
  graph: { key: "flow.graph.enabled", toBool: true },
  notion: { key: "flow.notion.enabled", toBool: true },
  r2: { key: "flow.r2.enabled", toBool: true },
};

/** 节点面板里不显示的设置键：节点头部开关已覆盖的键，以及不再让用户选的键。 */
export function hiddenSettingKeys(id: FlowStageId): string[] {
  const keys = SWITCH_MAP[id] ? [SWITCH_MAP[id]!.key] : [];
  // 向量库只提供 Milvus Lite；backend 只留作内部装配开关。
  if (id === "vector_store") keys.push("flow.vector_store.backend");
  return keys;
}

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
  external: "flow.external",
};

// 主干一行（row 2）：Zotero → 导入 → 向量化 → 向量库 → 检索/图谱 → 问答。
// 旁路：R2 备份在导入上方，Notion 在问答上方；模型分配与模型供应商自下而上接入问答；
// 联网补充在图谱下方，只在对话栏开关打开时接入问答（虚线）。
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
  external: { col: 5, row: 4 },
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
  { from: "external", to: "ask", labelKey: "flow_edge_external", dashed: true },
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
  remote: "线上 Rerank",
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
  remote: "Online Rerank",
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

/**
 * 画布与页头快捷按钮共用的节点状态：只留已知节点并按 idx 排序，再沿实线（非 dashed）边把上游的
 * degraded 向下游传播——一个错误配置的环节会让其后的环节也变黄而非保持绿色。按 idx 左→右即拓扑序。
 * 结果再算一遍不变（幂等）。
 */
export function effectiveStages(stages: PipelineStage[]): PipelineStage[] {
  const base = stages
    .filter((stage) => isFlowStageId(stage.id))
    .sort((a, b) => STAGE_META[a.id as FlowStageId].idx - STAGE_META[b.id as FlowStageId].idx);
  const incoming = new Map<FlowStageId, FlowStageId[]>();
  for (const edge of EDGES) {
    if (edge.dashed) continue; // 旁路/可选来源(dashed)不传播。
    const arr = incoming.get(edge.to) ?? [];
    arr.push(edge.from);
    incoming.set(edge.to, arr);
  }
  const eff = new Map<FlowStageId, FlowStageStatus>();
  return base.map((stage) => {
    const id = stage.id as FlowStageId;
    let status = stage.status;
    if (status === "ready") {
      for (const up of incoming.get(id) ?? []) {
        if (eff.get(up) === "degraded") { status = "degraded"; break; }
      }
    }
    eff.set(id, status);
    return status === stage.status ? stage : { ...stage, status };
  });
}

/** 页头快捷按钮的分组；chain 组按数据流向用 › 连接。需要处理的节点另放最左一组，不在这里。 */
export const JUMP_GROUPS: Array<{ labelKey: I18nKey; ids: FlowStageId[]; chain?: boolean }> = [
  { labelKey: "flow_jump_group_sources", ids: ["zotero", "external"] },
  {
    labelKey: "flow_jump_group_pipeline",
    ids: ["ingest", "embedding", "vector_store", "retrieval", "graph", "ask"],
    chain: true,
  },
  { labelKey: "flow_jump_group_models", ids: ["providers", "models"] },
  { labelKey: "flow_jump_group_sync", ids: ["notion", "r2"] },
];

/** 快捷按钮上的短名（节点标题较长，按钮放不下）；完整标题放在按钮提示里。 */
export const JUMP_LABEL_KEYS: Record<FlowStageId, I18nKey> = {
  zotero: "flow_jump_zotero",
  ingest: "flow_jump_ingest",
  embedding: "flow_jump_embedding",
  vector_store: "flow_jump_vector_store",
  retrieval: "flow_jump_retrieval",
  graph: "flow_jump_graph",
  ask: "flow_jump_ask",
  models: "flow_jump_models",
  providers: "flow_jump_providers",
  notion: "flow_jump_notion",
  r2: "flow_jump_r2",
  external: "flow_jump_external",
};

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
  if (stage.id === "external" && typeof stage.detail.tools_enabled === "number") {
    parts.push(lang === "zh" ? `${stage.detail.tools_enabled} 个工具` : `${stage.detail.tools_enabled} tools`);
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
