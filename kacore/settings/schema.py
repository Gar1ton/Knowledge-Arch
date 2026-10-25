"""设置项登记表：每个设置项只在这里登记一次（替代 `_conf_schema.json` 与 ConfigKeyPolicy 表）。

键名两层归属：
- `flow.<node>.<field>`：数据流节点拥有的设置（节点面板从本表渲染）；
- `prefs.<group>.<field>`：偏好设置窗口里的应用级选项。

`target` 把新键映射到既有 `Config` 的 (section, key)：`XxxConfig` dataclass 保留，改从
`SettingsStore.to_config_raw()` 读值，因此业务层无需改动。默认值直接取自这些 dataclass，
保证换存储不改变行为。`owner` 标明谁消费该项：backend（后端）/ shell（桌面壳）/ frontend（界面）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from kacore.config import (
    NOTION_SYNC_PRESERVE,
    NOTION_SYNC_STRICT,
    VECTOR_BACKEND_LEXICAL,
    VECTOR_BACKEND_MILVUS,
    ZOTERO_ACCESS_LOCAL,
    ZOTERO_ACCESS_SERVER,
    ZOTERO_STORAGE_LINKED,
    ZOTERO_STORAGE_MANAGED,
    ZOTERO_STORAGE_ZOTMOOV,
    ZOTERO_SYNC_ARCHIVE,
    ZOTERO_SYNC_CONSERVATIVE,
    ZOTERO_SYNC_STRICT,
    AskAgentConfig,
    DeepThinkingConfig,
    EmbeddingConfig,
    EnhancedRecallConfig,
    GraphConfig,
    NotionSyncConfig,
    R2SyncConfig,
    RerankConfig,
    SourceStoreConfig,
    VectorDbConfig,
    ZoteroSyncConfig,
)

Kind = Literal["bool", "int", "float", "str", "text", "enum", "path", "secret", "model_ref"]
Consequence = Literal["none", "restart", "rebuild"]
Owner = Literal["backend", "shell", "frontend"]

CONSEQUENCE_NONE = "none"
CONSEQUENCE_RESTART = "restart"
CONSEQUENCE_REBUILD = "rebuild"
# embedding.source 可取的字面值之一（其余为 ModelRef）。
EMBEDDING_SOURCE_LOCAL = "local"
ZOTERO_SERVER_KEY_SECRET = "zotero.server_api_key"


@dataclass(frozen=True)
class SettingSpec:
    """一个设置项。

    kind      取值类型；secret 的值只进加密库，settings.json 只留引用；model_ref 为
              {"provider_id", "model"}（nullable=True 时可为 None，options 里列出的字面值也可取）。
    home      显示位置（数据流节点 / 偏好分组），也是 GET /api/settings/schema 的过滤键。
    target    写入既有 Config 原始 dict 的 (section, key)；可映射到多处
              （如设备同时给 embedding 与 rerank）。
    portable  是否随 R2 快照跨设备携带（机密、本机路径与 R2 自身凭据不带）。
    secret_ref 机密在加密库中的条目名；空则使用 key 本身。
    """

    key: str
    kind: Kind
    default: Any
    home: str
    tier: Literal["basic", "advanced"] = "basic"
    consequence: Consequence = CONSEQUENCE_NONE
    options: tuple[str, ...] = ()
    bounds: tuple[float, float] | None = None
    target: tuple[tuple[str, str], ...] = ()
    portable: bool = True
    owner: Owner = "backend"
    nullable: bool = False
    secret_ref: str = ""

    @property
    def label_key(self) -> str:
        """前端 i18n.ts 的键：`set_` + key 中的点换成下划线。"""
        return "set_" + self.key.replace(".", "_")

    @property
    def secret_name(self) -> str:
        return self.secret_ref or self.key

    def to_public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "default": None if self.kind == "secret" else self.default,
            "home": self.home,
            "tier": self.tier,
            "consequence": self.consequence,
            "options": list(self.options),
            "bounds": list(self.bounds) if self.bounds else None,
            "label_key": self.label_key,
            "owner": self.owner,
            "nullable": self.nullable,
        }


def _t(section: str, key: str) -> tuple[tuple[str, str], ...]:
    return ((section, key),)


_R = CONSEQUENCE_RESTART
_B = CONSEQUENCE_REBUILD
_MAX_INT = 10**9

SPECS: tuple[SettingSpec, ...] = (
    # ── 模型分配（3 个角色；research / graph 为空 = 跟随 chat）────────────
    SettingSpec("flow.models.chat", "model_ref", None, "flow.models", nullable=True),
    SettingSpec("flow.models.research", "model_ref", None, "flow.models", nullable=True),
    SettingSpec("flow.models.graph", "model_ref", None, "flow.models", nullable=True),
    # ── 本地导入 ────────────────────────────────────────────────────
    SettingSpec(
        "flow.import.default_collection", "str", SourceStoreConfig.default_collection,
        "flow.import", consequence=_R, target=_t("source_store", "default_collection"),
    ),
    SettingSpec(
        "flow.import.ocr_enabled", "bool", SourceStoreConfig.ocr_enabled, "flow.import",
        consequence=_R, target=_t("source_store", "ocr_enabled"),
    ),
    # ── Zotero ──────────────────────────────────────────────────────
    SettingSpec(
        "flow.zotero.enabled", "bool", ZoteroSyncConfig.enabled, "flow.zotero",
        consequence=_R, target=_t("zotero_sync", "enabled"),
    ),
    SettingSpec(
        "flow.zotero.access_mode", "enum", ZoteroSyncConfig.access_mode, "flow.zotero",
        consequence=_R, options=(ZOTERO_ACCESS_LOCAL, ZOTERO_ACCESS_SERVER),
        target=_t("zotero_sync", "access_mode"),
    ),
    SettingSpec(
        "flow.zotero.data_dir", "path", ZoteroSyncConfig.zotero_data_dir, "flow.zotero",
        tier="advanced", consequence=_R, target=_t("zotero_sync", "zotero_data_dir"),
        portable=False,
    ),
    SettingSpec(
        "flow.zotero.api_port", "int", ZoteroSyncConfig.api_port, "flow.zotero",
        tier="advanced", consequence=_R, bounds=(1, 65535), target=_t("zotero_sync", "api_port"),
        portable=False,
    ),
    SettingSpec(
        "flow.zotero.server_api_key", "secret", "", "flow.zotero",
        secret_ref=ZOTERO_SERVER_KEY_SECRET, portable=False,
    ),
    SettingSpec(
        "flow.zotero.storage_mode", "enum", ZoteroSyncConfig.storage_mode, "flow.zotero",
        consequence=_B,
        options=(ZOTERO_STORAGE_MANAGED, ZOTERO_STORAGE_LINKED, ZOTERO_STORAGE_ZOTMOOV),
        target=_t("zotero_sync", "storage_mode"),
    ),
    SettingSpec(
        "flow.zotero.linked_root", "path", ZoteroSyncConfig.linked_root, "flow.zotero",
        consequence=_B, target=_t("zotero_sync", "linked_root"), portable=False,
    ),
    SettingSpec(
        "flow.zotero.zotmoov_root", "path", ZoteroSyncConfig.zotmoov_root, "flow.zotero",
        consequence=_B, target=_t("zotero_sync", "zotmoov_root"), portable=False,
    ),
    SettingSpec(
        "flow.zotero.sync_mode", "enum", ZoteroSyncConfig.sync_mode, "flow.zotero",
        consequence=_B, options=(ZOTERO_SYNC_STRICT, ZOTERO_SYNC_CONSERVATIVE, ZOTERO_SYNC_ARCHIVE),
        target=_t("zotero_sync", "sync_mode"),
    ),
    SettingSpec(
        "flow.zotero.auto_sync", "bool", ZoteroSyncConfig.auto_sync_enabled, "flow.zotero",
        consequence=_R, target=_t("zotero_sync", "auto_sync_enabled"),
    ),
    SettingSpec(
        "flow.zotero.auto_sync_interval_sec", "int", ZoteroSyncConfig.auto_sync_interval_sec,
        "flow.zotero", tier="advanced", consequence=_R, bounds=(60, _MAX_INT),
        target=_t("zotero_sync", "auto_sync_interval_sec"),
    ),
    # ── Embedding（来源 local 或一个连接上的模型）─────────────────────
    SettingSpec(
        # 默认线上 API（None = 尚未选连接）；"local" 为本地模型。
        "flow.embedding.source", "model_ref", None, "flow.embedding",
        consequence=_B, options=(EMBEDDING_SOURCE_LOCAL,), nullable=True,
    ),
    SettingSpec(
        "flow.embedding.local_model", "str", EmbeddingConfig.model, "flow.embedding",
        consequence=_B,
    ),
    SettingSpec(
        "flow.embedding.max_token_size", "int", EmbeddingConfig.max_token_size, "flow.embedding",
        tier="advanced", consequence=_B, bounds=(1, 1_000_000),
        target=_t("embedding", "max_token_size"),
    ),
    # ── 向量库 ──────────────────────────────────────────────────────
    SettingSpec(
        "flow.vector_store.backend", "enum", VectorDbConfig.backend, "flow.vector_store",
        consequence=_R, options=(VECTOR_BACKEND_MILVUS, VECTOR_BACKEND_LEXICAL),
        target=_t("vector_db", "backend"),
    ),
    SettingSpec(
        "flow.vector_store.auto_index", "bool", VectorDbConfig.auto_index_enabled,
        "flow.vector_store", consequence=_R, target=_t("vector_db", "auto_index_enabled"),
    ),
    SettingSpec(
        "flow.vector_store.auto_rebuild", "bool", VectorDbConfig.auto_rebuild_enabled,
        "flow.vector_store", target=_t("vector_db", "auto_rebuild_enabled"),
    ),
    SettingSpec(
        "flow.vector_store.auto_rebuild_delay_sec", "int",
        VectorDbConfig.auto_rebuild_delay_seconds, "flow.vector_store", tier="advanced",
        bounds=(5, 86400), target=_t("vector_db", "auto_rebuild_delay_seconds"),
    ),
    # ── 知识图谱 ────────────────────────────────────────────────────
    SettingSpec(
        "flow.graph.enabled", "bool", GraphConfig.enabled, "flow.graph", consequence=_R,
        target=_t("graph", "enabled"),
    ),
    SettingSpec(
        "flow.graph.query_mode", "enum", GraphConfig.query_mode, "flow.graph", consequence=_R,
        options=("mix", "local", "global", "hybrid", "naive", "bypass"),
        target=_t("graph", "query_mode"),
    ),
    SettingSpec(
        "flow.graph.max_doc_chars", "int", GraphConfig.max_doc_chars, "flow.graph",
        tier="advanced", consequence=_B, bounds=(0, _MAX_INT), target=_t("graph", "max_doc_chars"),
    ),
    SettingSpec(
        "flow.graph.llm_max_async", "int", GraphConfig.llm_max_async, "flow.graph",
        tier="advanced", consequence=_R, bounds=(1, 64), target=_t("graph", "llm_max_async"),
    ),
    SettingSpec(
        "flow.graph.embedding_max_async", "int", GraphConfig.embedding_max_async, "flow.graph",
        tier="advanced", consequence=_R, bounds=(1, 64),
        target=_t("graph", "embedding_max_async"),
    ),
    SettingSpec(
        "flow.graph.llm_timeout_sec", "int", GraphConfig.lightrag_llm_timeout_seconds,
        "flow.graph", tier="advanced", consequence=_R, bounds=(1, 86400),
        target=_t("graph", "lightrag_llm_timeout_seconds"),
    ),
    # ── 检索（rerank / 增强召回 / 深度思考）───────────────────────────
    SettingSpec(
        # 默认线上 Rerank；没选连接时设置桥按 noop 处理。
        "flow.retrieval.rerank.provider", "enum", "remote", "flow.retrieval",
        options=("auto", "cross_encoder", "remote", "noop"), target=_t("rerank", "provider"),
    ),
    # provider=remote 时用的连接与模型（地址、密钥由设置桥从连接注入）。
    SettingSpec(
        "flow.retrieval.rerank.remote", "model_ref", None, "flow.retrieval", nullable=True,
    ),
    SettingSpec(
        "flow.retrieval.rerank.model", "str", RerankConfig.model, "flow.retrieval",
        tier="advanced", target=_t("rerank", "model"),
    ),
    SettingSpec(
        "flow.retrieval.rerank.batch_size", "int", RerankConfig.batch_size, "flow.retrieval",
        tier="advanced", consequence=_R, bounds=(1, 1024), target=_t("rerank", "batch_size"),
    ),
    SettingSpec(
        "flow.retrieval.rerank.max_candidates", "int", RerankConfig.max_candidates,
        "flow.retrieval", tier="advanced", consequence=_R, bounds=(1, 1000),
        target=_t("rerank", "max_candidates"),
    ),
    SettingSpec(
        "flow.retrieval.rerank.keep", "int", RerankConfig.keep, "flow.retrieval",
        tier="advanced", consequence=_R, bounds=(1, 1000), target=_t("rerank", "keep"),
    ),
    SettingSpec(
        "flow.retrieval.enhanced_recall.max_sub_queries", "int",
        EnhancedRecallConfig.max_sub_queries, "flow.retrieval", tier="advanced", bounds=(1, 32),
        target=_t("enhanced_recall", "max_sub_queries"),
    ),
    SettingSpec(
        "flow.retrieval.enhanced_recall.wide_top_k", "int", EnhancedRecallConfig.wide_top_k,
        "flow.retrieval", tier="advanced", bounds=(1, 500),
        target=_t("enhanced_recall", "wide_top_k"),
    ),
    SettingSpec(
        "flow.retrieval.enhanced_recall.max_final_evidence", "int",
        EnhancedRecallConfig.max_final_evidence, "flow.retrieval", tier="advanced",
        bounds=(1, 200), target=_t("enhanced_recall", "max_final_evidence"),
    ),
    SettingSpec(
        "flow.retrieval.enhanced_recall.rerank_weight", "float",
        EnhancedRecallConfig.rerank_weight, "flow.retrieval", tier="advanced", bounds=(0.0, 1.0),
        target=_t("enhanced_recall", "rerank_weight"),
    ),
    SettingSpec(
        "flow.retrieval.enhanced_recall.corrective_enabled", "bool",
        EnhancedRecallConfig.corrective_enabled, "flow.retrieval", tier="advanced",
        target=_t("enhanced_recall", "corrective_enabled"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.max_rounds", "int", DeepThinkingConfig.max_rounds,
        "flow.retrieval", tier="advanced", bounds=(1, 32), target=_t("deep_thinking", "max_rounds"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.max_sub_queries", "int", DeepThinkingConfig.max_sub_queries,
        "flow.retrieval", tier="advanced", bounds=(1, 32),
        target=_t("deep_thinking", "max_sub_queries"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.wide_top_k", "int", DeepThinkingConfig.wide_top_k,
        "flow.retrieval", tier="advanced", bounds=(1, 500),
        target=_t("deep_thinking", "wide_top_k"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.rerank_weight", "float", DeepThinkingConfig.rerank_weight,
        "flow.retrieval", tier="advanced", bounds=(0.0, 1.0),
        target=_t("deep_thinking", "rerank_weight"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.verify_enabled", "bool", DeepThinkingConfig.verify_enabled,
        "flow.retrieval", tier="advanced", target=_t("deep_thinking", "verify_enabled"),
    ),
    SettingSpec(
        "flow.retrieval.deep_thinking.max_verify_rounds", "int",
        DeepThinkingConfig.max_verify_rounds, "flow.retrieval", tier="advanced", bounds=(0, 16),
        target=_t("deep_thinking", "max_verify_rounds"),
    ),
    # ── 回答 ────────────────────────────────────────────────────────
    SettingSpec(
        "flow.answer.language", "enum", AskAgentConfig.answer_language, "flow.answer",
        options=("auto", "zh", "en"), target=_t("ask", "answer_language"),
    ),
    SettingSpec(
        "flow.answer.system_prompt", "text", AskAgentConfig.system_prompt, "flow.answer",
        target=_t("ask", "system_prompt"),
    ),
    SettingSpec(
        "flow.answer.llm_timeout_sec", "int", AskAgentConfig.llm_timeout_seconds, "flow.answer",
        tier="advanced", consequence=_R, bounds=(0, 86400), target=_t("ask", "llm_timeout_seconds"),
    ),
    SettingSpec(
        "flow.answer.task_timeout_sec", "int", AskAgentConfig.task_timeout_seconds,
        "flow.answer", tier="advanced", bounds=(0, 86400), target=_t("ask", "task_timeout_seconds"),
    ),
    # ── Notion ──────────────────────────────────────────────────────
    SettingSpec(
        "flow.notion.enabled", "bool", NotionSyncConfig.enabled, "flow.notion", consequence=_R,
        target=_t("notion_sync", "enabled"),
    ),
    SettingSpec("flow.notion.token", "secret", "", "flow.notion", consequence=_R, portable=False),
    SettingSpec(
        "flow.notion.database_id", "str", NotionSyncConfig.database_id, "flow.notion",
        target=_t("notion_sync", "database_id"),
    ),
    SettingSpec(
        "flow.notion.qa_database_id", "str", NotionSyncConfig.qa_database_id, "flow.notion",
        target=_t("notion_sync", "qa_database_id"),
    ),
    SettingSpec(
        "flow.notion.data_source_id", "str", NotionSyncConfig.data_source_id, "flow.notion",
        tier="advanced", target=_t("notion_sync", "data_source_id"),
    ),
    SettingSpec(
        "flow.notion.qa_data_source_id", "str", NotionSyncConfig.qa_data_source_id,
        "flow.notion", tier="advanced", target=_t("notion_sync", "qa_data_source_id"),
    ),
    SettingSpec(
        "flow.notion.parent_page_id", "str", NotionSyncConfig.parent_page_id, "flow.notion",
        target=_t("notion_sync", "parent_page_id"),
    ),
    SettingSpec(
        "flow.notion.database_title", "str", NotionSyncConfig.database_title, "flow.notion",
        tier="advanced", target=_t("notion_sync", "database_title"),
    ),
    SettingSpec(
        "flow.notion.sync_mode", "enum", NotionSyncConfig.sync_mode, "flow.notion",
        options=(NOTION_SYNC_PRESERVE, NOTION_SYNC_STRICT), target=_t("notion_sync", "sync_mode"),
    ),
    SettingSpec(
        "flow.notion.auto_sync", "bool", NotionSyncConfig.auto_sync_enabled, "flow.notion",
        target=_t("notion_sync", "auto_sync_enabled"),
    ),
    SettingSpec(
        "flow.notion.auto_sync_interval_sec", "int", NotionSyncConfig.auto_sync_interval_sec,
        "flow.notion", tier="advanced", bounds=(0, _MAX_INT),
        target=_t("notion_sync", "auto_sync_interval_sec"),
    ),
    # ── R2 备份 ────────────────────────────────────────────────────
    SettingSpec(
        "flow.r2.enabled", "bool", R2SyncConfig.enabled, "flow.r2", consequence=_R,
        target=_t("r2_sync", "enabled"), portable=False,
    ),
    SettingSpec(
        "flow.r2.account_id", "str", R2SyncConfig.account_id, "flow.r2", consequence=_R,
        target=_t("r2_sync", "account_id"), portable=False,
    ),
    SettingSpec(
        "flow.r2.access_key_id", "secret", "", "flow.r2", consequence=_R,
        target=_t("r2_sync", "access_key_id"), portable=False,
    ),
    SettingSpec(
        "flow.r2.secret_access_key", "secret", "", "flow.r2", consequence=_R,
        target=_t("r2_sync", "secret_access_key"), portable=False,
    ),
    SettingSpec(
        "flow.r2.bucket", "str", R2SyncConfig.bucket, "flow.r2", consequence=_R,
        target=_t("r2_sync", "bucket"), portable=False,
    ),
    SettingSpec(
        "flow.r2.warn_threshold", "float", R2SyncConfig.warn_threshold, "flow.r2",
        tier="advanced", consequence=_R, bounds=(0.0, 1.0), target=_t("r2_sync", "warn_threshold"),
        portable=False,
    ),
    SettingSpec(
        "flow.r2.backup_interval_sec", "int", R2SyncConfig.backup_interval_sec, "flow.r2",
        tier="advanced", consequence=_R, bounds=(0, _MAX_INT),
        target=_t("r2_sync", "backup_interval_sec"), portable=False,
    ),
    # ── 偏好设置：通用 / 外观（桌面壳与界面消费）──────────────────────
    SettingSpec(
        "prefs.general.language", "enum", "en", "prefs.general", options=("zh", "en"),
        owner="frontend",
    ),
    SettingSpec("prefs.general.close_to_tray", "bool", True, "prefs.general", owner="shell"),
    SettingSpec(
        "prefs.general.launch_at_login", "bool", False, "prefs.general", owner="shell",
        portable=False,
    ),
    SettingSpec("prefs.general.check_updates", "bool", True, "prefs.general", owner="shell"),
    # 允许 Codex / Claude Code 等本机 AI 助手经连接文件接入（ka_server/agent_access.py）；即时生效。
    SettingSpec("prefs.general.agent_access", "bool", False, "prefs.general", portable=False),
    SettingSpec(
        "prefs.appearance.theme_mode", "enum", "dark", "prefs.appearance",
        options=("system", "light", "dark"), owner="frontend",
    ),
    SettingSpec("prefs.appearance.palette", "str", "", "prefs.appearance", owner="frontend"),
    SettingSpec(
        "prefs.appearance.ui_scale", "float", 1.0, "prefs.appearance", bounds=(0.8, 1.5),
        owner="frontend",
    ),
    # ── 偏好设置：存储 / 本地模型 / 诊断 ───────────────────────────────
    SettingSpec(
        "prefs.storage.data_dir", "path", "", "prefs.storage", consequence=_R, owner="shell",
        portable=False,
    ),
    SettingSpec(
        "prefs.storage.embedding_cache_mb", "int", EmbeddingConfig.cache_mb, "prefs.storage",
        consequence=_R, bounds=(0, 1_000_000), target=_t("embedding", "cache_mb"),
    ),
    SettingSpec(
        "prefs.local_models.device", "enum", "auto", "prefs.local_models", consequence=_R,
        options=("auto", "cpu", "cuda", "mps"),
        target=(("embedding", "device"), ("rerank", "device")), portable=False,
    ),
    SettingSpec(
        "prefs.local_models.idle_unload_sec", "int", EmbeddingConfig.local_idle_timeout_seconds,
        "prefs.local_models", consequence=_R, bounds=(0, 86400),
        target=_t("embedding", "local_idle_timeout_seconds"),
    ),
    SettingSpec(
        "prefs.local_models.load_timeout_sec", "int", EmbeddingConfig.load_timeout_seconds,
        "prefs.local_models", consequence=_R, bounds=(0, 3600),
        target=_t("embedding", "load_timeout_seconds"),
    ),
    SettingSpec(
        "prefs.diagnostics.log_level", "enum", "info", "prefs.diagnostics", consequence=_R,
        options=("info", "debug"),
    ),
    SettingSpec(
        "prefs.diagnostics.log_keep_days", "int", 14, "prefs.diagnostics", consequence=_R,
        bounds=(1, 365),
    ),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SPECS}
SPECS_BY_TARGET: dict[tuple[str, str], SettingSpec] = {
    target: spec for spec in SPECS for target in spec.target
}
FLOW_HOMES = tuple(dict.fromkeys(s.home for s in SPECS if s.home.startswith("flow.")))
PREFS_HOMES = tuple(dict.fromkeys(s.home for s in SPECS if s.home.startswith("prefs.")))


def spec_for(key: str) -> SettingSpec:
    """按键取 spec；未登记的键抛 KeyError（调用方转成 400）。"""
    return SPECS_BY_KEY[key]


def specs_for_home(home: str | None = None) -> list[SettingSpec]:
    """home 为空返回全部；`flow` / `prefs` 返回整层；否则精确匹配节点或分组。"""
    if not home:
        return list(SPECS)
    if home in {"flow", "prefs"}:
        return [s for s in SPECS if s.home.startswith(home + ".")]
    return [s for s in SPECS if s.home == home]


__all__ = [
    "CONSEQUENCE_NONE",
    "CONSEQUENCE_REBUILD",
    "CONSEQUENCE_RESTART",
    "EMBEDDING_SOURCE_LOCAL",
    "FLOW_HOMES",
    "PREFS_HOMES",
    "SPECS",
    "SPECS_BY_KEY",
    "SPECS_BY_TARGET",
    "ZOTERO_SERVER_KEY_SECRET",
    "SettingSpec",
    "spec_for",
    "specs_for_home",
]
