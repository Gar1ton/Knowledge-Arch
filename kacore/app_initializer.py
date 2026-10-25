"""组合根（Composition Root，见 ../ARCHITECTURE.md §3）。

【唯一】把零件 new 出来并接起来的地方：按依赖顺序构造、构造器注入、生命周期对称。
业务层不出现在本文件之外的任何「装配代码」。

装配数据库、仓储、检索编排、索引、同步目标与周期任务。HTTP 服务不归本类所有：
由 `ka_server` 薄壳在 initialize() 之后构造并在 teardown() 之前关闭。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from kacore.api import KnowledgeRepositoryApi
from kacore.ask_progress import ProgressStore

# 依赖探测收口至能力注册表；保留模块内 _module_available 名以兼容既有测试的 monkeypatch。
from kacore.capabilities import milvus_runtime_status
from kacore.capabilities import module_available as _module_available
from kacore.config import Config
from kacore.domain.models import Collection, SyncTargetKind
from kacore.managers.category_manager import CategoryManager
from kacore.managers.ingest_manager import IngestManager
from kacore.managers.quota_manager import QuotaManager
from kacore.migration_runner import run_migrations
from kacore.pipelines.sync_pipeline import SyncPipeline
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore
from kacore.repository.sync_targets.r2 import R2SyncTarget

if TYPE_CHECKING:
    from kacore.extras.manager import ExtrasManager
    from kacore.index_compatibility import IndexCompatibilityStore
    from kacore.pipelines.retrieval_orchestrator import RetrievalOrchestrator
    from kacore.repository.embedding.base import EmbeddingProvider
    from kacore.repository.source_store.base import SourceDocumentStore
    from kacore.repository.vector_store.base import VectorStore
    from kacore.settings.base import SecretStore
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("AppInitializer")

# 维度探针文本：内容无所谓，只为拿到一条真实向量的长度。
_EMBEDDING_PROBE_TEXT = "knowledge-repository-dimension-probe"


class _EmbeddingProbeTimeout(RuntimeError):
    """维度探针在配置时限内未返回；消息已是面向用户的中文说明。"""


class AppInitializer:
    """装配全部子系统并拥有其生命周期。

    settings 是设置的唯一来源（settings.json + 加密库）；`Config` 由它桥接生成，
    每次 initialize() / reload() 重新生成，保证读到最新的持久化值。
    """

    def __init__(
        self,
        settings: SettingsStore,
        data_dir: Path,
        *,
        extras: ExtrasManager | None = None,
    ) -> None:
        from kacore.providers.registry import ProviderRegistry
        from kacore.providers.router import ModelRouter

        self._data_dir = data_dir
        self._settings = settings
        # 可选组件管理器由进程薄壳构造（需在 initialize 之前激活 sys.path），这里只转交门面。
        self.extras_manager = extras
        self._config = Config(settings.to_config_raw())
        self.provider_registry = ProviderRegistry(settings)
        self.model_router = ModelRouter(settings, self.provider_registry)
        self._exit_stack: AsyncExitStack | None = None
        self._backup_task: asyncio.Task[Any] | None = None
        self._zotero_sync_task: asyncio.Task[Any] | None = None
        self._notion_sync_task: asyncio.Task[Any] | None = None
        self._auto_reindex_task: asyncio.Task[Any] | None = None
        self.auto_reindex_scheduler: Any | None = None
        # 超时后仍在后台跑的维度探针（模型下载不可中断，只能让它跑完再补记结果）。
        self._pending_probe_task: asyncio.Task[Any] | None = None
        self.zotero_sync_pipeline: Any | None = None
        self.notion_sync_pipeline: Any | None = None
        self._log_handler: Any | None = None

        # 子系统句柄 —— 在 initialize() 中按依赖顺序赋值，供 ka_server / web 引用。
        self.source_store: SourceDocumentStore | None = None
        self.api: KnowledgeRepositoryApi | None = None
        self.lightrag_registry: Any | None = None
        self.vector_store: VectorStore | None = None
        # 最近一次 Milvus 装配失败的具体异常文本；vector_store 装配成功时清空。
        # 供 capabilities 面板展示具体原因（而非套话），以及「清除残留锁」的前置守卫。
        self._vector_store_init_error: str | None = None
        self.embedding_provider: EmbeddingProvider | None = None
        self.embedding_dimension: int | None = None
        self.embedding_fingerprint: str | None = None
        self.index_compatibility: IndexCompatibilityStore | None = None
        self.retrieval_orchestrator: RetrievalOrchestrator | None = None
        self.progress_store: ProgressStore = ProgressStore()
        self.secret_store: SecretStore = settings.secrets

        # 依赖句柄
        self.ingest_manager: IngestManager | None = None
        self.category_manager: CategoryManager | None = None
        self.quota_manager: QuotaManager | None = None
        self.sync_pipeline: SyncPipeline | None = None
        self.r2_backup_manager: Any | None = None

    @property
    def config(self) -> Config:
        """对外暴露 typed config 门面（只读）。"""
        return self._config

    @property
    def settings(self) -> SettingsStore:
        return self._settings

    @property
    def log_handler(self) -> Any | None:
        """initialize() 安装的内存日志 handler（供 HTTP 层的日志页复用同一实例）。"""
        return self._log_handler

    # ── 启动：按依赖顺序构造 ────────────────────────────────────
    async def initialize(self) -> None:
        # 0) 最优先安装日志 handler，确保后续所有启动日志可被终端页捕获。
        from kacore.log_capture import install as _install_log_capture

        self._log_handler = _install_log_capture()
        logger.info("AppInitializer.initialize() started")

        # 启动是一条长链路，任何一环卡住都表现为「重启没反应」。逐段落记累计耗时，
        # 让终端页最后一条 INFO 直接指出卡在哪一步。
        init_started = time.monotonic()

        def _stage_done(stage: str) -> None:
            logger.info(
                "startup step done: %s (%.1fs so far)", stage, time.monotonic() - init_started
            )

        # 完整恢复必须发生在 SQLite/Milvus/LightRAG 建立连接之前。
        self._data_dir.mkdir(parents=True, exist_ok=True)
        from kacore.managers.r2_backup_manager import apply_pending_restore

        if await asyncio.to_thread(apply_pending_restore, self._data_dir, self._config):
            self._settings.reload()
            self._config = Config(self._settings.to_config_raw())
            logger.info("Pending R2 restore applied; continuing startup with the restored settings")

        # 旧版插件数据导入：staging 已在上一轮准备好，这里在连接数据库前换入。
        from kacore.legacy_import.apply import apply_pending_legacy_import

        if await asyncio.to_thread(apply_pending_legacy_import, self._data_dir, self._settings):
            self._config = Config(self._settings.to_config_raw())
            logger.info("Old plugin data swapped in; continuing startup with the imported settings")

        self._exit_stack = AsyncExitStack()

        # 1) 解析各子系统专属 typed config（此处已可用，供后续构造使用）。
        source_cfg = self._config.get_source_store_config()
        r2_cfg = self._config.get_r2_sync_config()
        notion_cfg = self._config.get_notion_sync_config()

        _ask_cfg = self._config.get_ask_agent_config()

        # 2) 无依赖层先行：DB 连接 + 迁移 + 仓储生产实现（v0.3.0 接入 sqlite）。
        db_path = self._data_dir / source_cfg.db_filename

        db = await self._exit_stack.enter_async_context(aiosqlite.connect(str(db_path)))
        await db.execute("PRAGMA foreign_keys = ON")
        await run_migrations(db)

        self.source_store = SQLiteSourceDocumentStore(db)
        interrupted = await self.source_store.mark_interrupted_build_jobs()
        if interrupted > 0:
            logger.warning(
                "%d graph build job(s) were interrupted by a process restart. Starting the build "
                "again resumes from where it stopped (pending/error documents only).",
                interrupted,
            )
        existing_collections = {
            collection.name for collection in await self.source_store.list_collections()
        }
        if source_cfg.default_collection not in existing_collections:
            await self.source_store.upsert_collection(
                Collection(
                    name=source_cfg.default_collection,
                    description="Default collection",
                )
            )
        _stage_done(f"SQLite migrations and repositories ({db_path.name})")

        # 3) 构造同步目标
        from kacore.repository.sync_targets.base import SyncTarget

        sync_targets: dict[SyncTargetKind, SyncTarget] = {}
        # R2SyncTarget 延迟初始化 boto3.client，故总是先实例化并配置。
        r2_target = R2SyncTarget(r2_cfg)
        sync_targets[SyncTargetKind.R2] = r2_target

        from kacore.adapters.notion_rest import NotionRestClient
        from kacore.repository.sync_targets.notion import NotionSyncTarget

        # Notion 直连 REST API；token 每次请求从设置现取（节点里换 token 无需重启）。
        settings = self._settings
        notion_client = NotionRestClient(
            lambda: str(settings.get("flow.notion.token") or ""),
            rate_limit_rps=notion_cfg.rate_limit_rps,
        )
        notion_target = NotionSyncTarget(
            config=notion_cfg, source_store=self.source_store, adapter=notion_client
        )
        sync_targets[SyncTargetKind.NOTION] = notion_target

        # 4) 依赖前者的编排层 managers/pipelines
        graph_cfg = self._config.get_graph_config()

        self.ingest_manager = IngestManager(
            source_store=self.source_store,
            config=source_cfg,
            data_dir=self._data_dir,
        )
        self.category_manager = CategoryManager(source_store=self.source_store)
        self.quota_manager = QuotaManager(sync_targets=sync_targets, r2_config=r2_cfg)
        self.sync_pipeline = SyncPipeline(
            source_store=self.source_store,
            sync_targets=sync_targets,
            quota_manager=self.quota_manager,
            db_path=db_path,
        )

        _stage_done("sync targets and orchestration (managers/pipelines)")

        # 4.5) 三个 LLM 角色都经「模型分配」路由；未分配时 LLMAdapter 走离线占位。
        # RoleClient 每次调用现取分配与密钥，改选模型 / 换密钥无需重启。
        from kacore.adapters.llm import LLMAdapter

        llm_adapter = LLMAdapter(
            self.model_router.client_for("chat"), timeout_seconds=_ask_cfg.llm_timeout_seconds
        )
        research_llm_adapter = LLMAdapter(
            self.model_router.client_for("research"),
            timeout_seconds=_ask_cfg.llm_timeout_seconds,
        )
        logger.info(
            "model assignment: chat=%s research=%s graph=%s",
            self.model_router.describe("chat") or "unassigned",
            self.model_router.describe("research") or "follows chat",
            self.model_router.describe("graph") or "follows chat",
        )

        # 4.6) 构造共享 Embedding，并用真实探针维度装配 Milvus/LightRAG。
        vdb_cfg = self._config.get_vector_db_config()
        embedding_cfg = self._config.get_embedding_config()
        from kacore.index_compatibility import (
            IndexCompatibilityStore,
            embedding_fingerprint,
        )

        self.index_compatibility = IndexCompatibilityStore(
            self._data_dir / "index_compatibility.json"
        )
        local_missing = embedding_cfg.provider == "local" and not _module_available(
            "sentence_transformers"
        )
        online_unset = embedding_cfg.provider == "external" and not embedding_cfg.base_url
        embedding_runtime_available = not (local_missing or online_unset)
        if not embedding_runtime_available:
            logger.warning(
                "Embedding is not ready (%s): Milvus Lite is built in and turns on once embedding "
                "is set up; until then search uses SQLite keyword recall.",
                (
                    "no online API connection chosen yet"
                ) if online_unset else "local model component not installed",
            )

        if (vdb_cfg.backend == "milvus" or graph_cfg.enabled) and embedding_runtime_available:
            from kacore.repository.embedding.factory import EmbeddingProviderFactory

            logger.info(
                "starting embedding dimension probe: %s timeout %ss",
                self._config.describe_embedding(),
                embedding_cfg.load_timeout_seconds or "none",
            )

            probe_started = time.monotonic()
            try:
                self.embedding_provider = EmbeddingProviderFactory.create_provider(
                    self._config, db_dir=str(self._data_dir)
                )
                probe = await self._probe_embedding_dimension(
                    self.embedding_provider, embedding_cfg
                )
                if not probe:
                    raise RuntimeError("Embedding dimension probe returned an empty vector")
                self.embedding_dimension = len(probe)
                self.embedding_fingerprint = embedding_fingerprint(
                    embedding_cfg, self.embedding_dimension
                )
                self._config.set_embedding_dimension(self.embedding_dimension)
                logger.info(
                    "embedding dimension probe done: %s dim=%d in %.1fs",
                    self._config.describe_embedding(),
                    self.embedding_dimension,
                    time.monotonic() - probe_started,
                )
            except _EmbeddingProbeTimeout as exc:
                logger.error(
                    "Embedding dimension probe timed out (%ss); starting without vectors/graph: %s",
                    embedding_cfg.load_timeout_seconds,
                    exc,
                )
                self._config.add_diagnostic(str(exc))
                self.embedding_provider = None
            except NotImplementedError as exc:
                logger.warning(
                    "Embedding provider failed to initialize; graph and vector search are off: %s",
                    exc,
                )
                self._config.add_diagnostic(f"Embedding provider unavailable: {exc}")
                self.embedding_provider = None
            except Exception as exc:
                logger.error(
                    "Embedding provider raised during initialization; graph and vector search are "
                    "off: %s", exc, exc_info=True
                )
                self._config.add_diagnostic(f"Embedding dimension probe failed: {exc}")
                self.embedding_provider = None
            _stage_done("embedding probe")

        # 就绪判定收口至 capabilities（pymilvus ∧ milvus_lite）：只探 pymilvus 会在 Windows
        # 上放行一个必然抛 ConnectionConfigException 的装配，并让依赖面板显示假绿灯。
        milvus_runtime = milvus_runtime_status()
        if (
            vdb_cfg.backend == "milvus"
            and self.embedding_provider is not None
            and self.embedding_dimension is not None
            and milvus_runtime["ready"]
        ):
            from kacore.repository.vector_store.milvus_lite import (
                MilvusLiteVectorStore,
                MilvusSchemaMismatchError,
            )

            milvus_path = self._data_dir / vdb_cfg.db_filename
            self._vector_store_init_error = None
            is_new_index = not milvus_path.exists()
            milvus = None
            try:
                milvus = MilvusLiteVectorStore(
                    db_path=str(milvus_path),
                    dim=self.embedding_dimension,
                )
                milvus.validate_schema()
                self.vector_store = milvus
                created_collection = bool(
                    getattr(milvus, "created_collection", is_new_index)
                )
                source_has_documents = bool(await self.source_store.list_documents())
                if (
                    created_collection
                    and source_has_documents
                    and self.embedding_fingerprint
                ):
                    self.index_compatibility.mark_milvus_incompatible(
                        "Milvus collection was recreated while SQLite contains documents."
                    )
                    self._config.add_diagnostic(
                        "Milvus collection is empty while SQLite contains documents; rebuild it."
                    )
                    await self._mark_all_documents_needs_reindex()
                elif created_collection and self.embedding_fingerprint:
                    self.index_compatibility.mark_milvus_compatible(
                        self.embedding_fingerprint
                    )
                elif (
                    self.embedding_fingerprint
                    and not self.index_compatibility.is_milvus_compatible(
                        self.embedding_fingerprint
                    )
                ):
                    self._config.add_diagnostic(
                        "Milvus index is incompatible with the active embedding; rebuild it."
                    )
                    await self._mark_all_documents_needs_reindex()
            except MilvusSchemaMismatchError as exc:
                logger.error(
                    "Milvus schema mismatch; retrieval is disabled until a full rebuild: %s",
                    exc,
                )
                self.index_compatibility.mark_milvus_incompatible(str(exc))
                self._config.add_diagnostic(str(exc))
                self.vector_store = milvus
                await self._mark_all_documents_needs_reindex()
            except Exception as exc:
                logger.error(
                    "Milvus initialization failed; SQLite lexical retrieval remains active: %s",
                    exc,
                )
                self._config.add_diagnostic(f"Milvus unavailable: {exc}")
                self._vector_store_init_error = str(exc)
                if milvus is not None:
                    try:
                        await milvus.close()
                    except Exception:
                        logger.exception("Failed to close partially initialized Milvus store")
                self.vector_store = None
        elif (
            vdb_cfg.backend == "milvus"
            and self.embedding_provider is not None
            and self.embedding_dimension is not None
        ):
            # 缺什么、怎么修，一次说清；不再笼统地说「未安装 Milvus Lite 可选依赖」。
            logger.warning("Local Milvus vector store unavailable: %s", milvus_runtime["hint"])
            self._config.add_diagnostic(f"Milvus unavailable: {milvus_runtime['hint']}")
        if vdb_cfg.backend == "milvus":
            _stage_done(
                f"Milvus vector store ({'ready' if self.vector_store is not None else 'inactive'})"
            )

        # 4.6.5) 构造官方 LightRAG Core registry（按 collection 懒加载实例）。
        if (
            graph_cfg.enabled
            and self.embedding_provider is not None
            and self.embedding_dimension is not None
        ):
            if not _module_available("lightrag"):
                logger.warning(
                    "graph.enabled=true but the LightRAG component is not installed; skipped "
                    "LightRAG Core."
                )
            else:
                from kacore.lightrag_core import LightRAGCoreRegistry

                # 图谱构建慢且长：单次超时与重试由 graph 角色的客户端负责，外层不再限时。
                lightrag_llm_adapter = LLMAdapter(
                    self.model_router.client_for(
                        "graph",
                        timeout_sec=graph_cfg.lightrag_llm_timeout_seconds,
                        max_retries=graph_cfg.lightrag_llm_max_retries,
                    ),
                    timeout_seconds=0,
                )

                self.lightrag_registry = LightRAGCoreRegistry(
                    config=graph_cfg,
                    data_dir=self._data_dir,
                    llm_adapter=lightrag_llm_adapter,
                    embedding_provider=self.embedding_provider,
                    embedding_dim=self.embedding_dimension,
                    max_token_size=embedding_cfg.max_token_size,
                    embedding_model=embedding_cfg.model,
                )
                incompatible_lightrag = [
                    collection
                    for collection in self.lightrag_registry.existing_collections()
                    if not self.embedding_fingerprint
                    or not self.index_compatibility.is_lightrag_compatible(
                        collection, self.embedding_fingerprint
                    )
                ]
                if incompatible_lightrag:
                    self._config.add_diagnostic(
                        "LightRAG indexes are incompatible with the active embedding; "
                        "rebuild affected collections."
                    )
        elif graph_cfg.enabled and self.embedding_provider is None:
            logger.warning(
                "graph.enabled=true but the embedding provider is unavailable; skipped LightRAG "
                "Core. Check the embedding settings (provider can be 'local' or 'external')."
            )

        # 4.7) 构造统一检索编排器 (RetrievalOrchestrator)
        from kacore.pipelines.retrieval_orchestrator import RetrievalOrchestrator

        self.retrieval_orchestrator = RetrievalOrchestrator(
            source_store=self.source_store,
            config=self._config,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            lightrag_registry=self.lightrag_registry,
            index_compatibility=self.index_compatibility,
            embedding_fingerprint=self.embedding_fingerprint,
        )

        # 4.8) 构造 reranker 与 Deep Thinking 编排器（FAIR-RAG 迭代检索）。
        # reranker 懒加载，构造不触发模型下载；缺依赖自动 noop，不影响普通 ask。
        from kacore.pipelines.deep_thinking_orchestrator import DeepThinkingOrchestrator
        from kacore.repository.reranker import build_reranker

        rerank_cfg = self._config.get_rerank_config()
        self.reranker = build_reranker(
            provider=rerank_cfg.provider,
            model=rerank_cfg.model,
            device=rerank_cfg.device,
            batch_size=rerank_cfg.batch_size,
            max_candidates=rerank_cfg.max_candidates,
            base_url=rerank_cfg.base_url,
            api_key=rerank_cfg.api_key,
        )
        dt_cfg = self._config.get_deep_thinking_config()
        dt_llm_adapter = research_llm_adapter
        self.deep_thinking_orchestrator = DeepThinkingOrchestrator(
            retrieval_orchestrator=self.retrieval_orchestrator,
            reranker=self.reranker,
            llm_adapter=dt_llm_adapter,
            dt_config=dt_cfg,
        )

        # 4.8a) Codex/外部 agent 的无生成式 LLM 证据召回器。只复用检索、reranker
        # 与各档证据预算；问题拆解、充分性判断和作答留给调用方 agent。
        from kacore.pipelines.agent_evidence import AgentEvidenceOrchestrator

        self.agent_evidence_orchestrator = AgentEvidenceOrchestrator(
            retrieval_orchestrator=self.retrieval_orchestrator,
            reranker=self.reranker,
            enhanced_config=self._config.get_enhanced_recall_config(),
            deep_config=dt_cfg,
        )

        # 4.8b) 增强召回编排器（A-RAG「2+1」中间档）。与 deep 共享 reranker 实例与
        # 独立 LLM endpoint 决策（同为研究型内部 agent，不重复膨胀配置面）。
        from kacore.pipelines.enhanced_recall_orchestrator import EnhancedRecallOrchestrator

        self.enhanced_recall_orchestrator = EnhancedRecallOrchestrator(
            retrieval_orchestrator=self.retrieval_orchestrator,
            reranker=self.reranker,
            llm_adapter=dt_llm_adapter,
            er_config=self._config.get_enhanced_recall_config(),
        )

        # 4.9) R2 完整备份管理器：inventory/manifest 是 R2 v1 的唯一恢复真相源。
        from kacore.managers.r2_backup_manager import R2BackupManager

        self.r2_backup_manager = R2BackupManager(
            target=r2_target,
            source_store=self.source_store,
            config=self._config,
            r2_config=r2_cfg,
            data_dir=self._data_dir,
            db_path=db_path,
            quiesce_indexes=self._quiesce_indexes_for_backup,
            reload_callback=self.reload,
            runtime_event_sink=self._log_handler,
            settings=self._settings,
        )

        # 5) 业务门面（依赖已装配的仓储/managers）。
        self.api = KnowledgeRepositoryApi(
            source_store=self.source_store,
            sync_targets=sync_targets,
            ingest_manager=self.ingest_manager,
            category_manager=self.category_manager,
            quota_manager=self.quota_manager,
            sync_pipeline=self.sync_pipeline,
            lightrag_registry=self.lightrag_registry,
            config=self._config,
            config_persist=self._persist_config_value,
            settings_store=self._settings,
            provider_registry=self.provider_registry,
            llm_adapter=llm_adapter,
            managed_documents_dir=self._data_dir / "library",
            vector_store=self.vector_store,
            vector_store_init_error=self._vector_store_init_error,
            embedding_provider=self.embedding_provider,
            retrieval_orchestrator=self.retrieval_orchestrator,
            deep_thinking_orchestrator=self.deep_thinking_orchestrator,
            enhanced_recall_orchestrator=self.enhanced_recall_orchestrator,
            agent_evidence_orchestrator=self.agent_evidence_orchestrator,
            reranker=self.reranker,
            progress_store=self.progress_store,
            index_compatibility=self.index_compatibility,
            embedding_fingerprint=self.embedding_fingerprint,
            secret_store=self.secret_store,
            reload_callback=self.reload,
            r2_backup_manager=self.r2_backup_manager,
            runtime_event_sink=self._log_handler,
            extras_manager=self.extras_manager,
            data_dir=self._data_dir,
        )
        await self.api.restore_paused_build_job()

        # 5.5) Zotero 单向 Pull 管线（api 构造后注入，回调引用 api 的索引/LRAG 助手）。
        from kacore.pipelines.zotero_sync_pipeline import ZoteroSyncPipeline

        self.zotero_sync_pipeline = ZoteroSyncPipeline(
            source_store=self.source_store,
            ingest_manager=self.ingest_manager,
            config=self._config,
            zotero_api_key_provider=self.api._zotero_server_key,
            web_cache_dir=self._data_dir / "zotero_web_cache",
            index_document=self.api._index_document,
            remove_index=self.api._remove_document_index,
            lightrag_cleanup=self.api._lightrag_cleanup,
            lightrag_mark_pending=self.api._mark_lightrag_pending,
        )
        self.api.attach_zotero_pipeline(self.zotero_sync_pipeline)

        # 5.55) Notion 单向推送管线（文章两表增量 + QA 暂存箱补推）。
        from kacore.pipelines.notion_sync_pipeline import NotionSyncPipeline

        self.notion_sync_pipeline = NotionSyncPipeline(
            source_store=self.source_store,
            notion_target=notion_target,
            config_provider=self._config.get_notion_sync_config,
        )
        self.api.attach_notion_sync_pipeline(self.notion_sync_pipeline)
        _stage_done("business API facade and retrieval orchestration")

        # 6) 周期任务（如 R2 周期备份，v0.3.0 起注册）。
        if r2_cfg.enabled and r2_cfg.backup_interval_sec > 0:
            self._backup_task = asyncio.create_task(
                self._periodic_backup(r2_cfg.backup_interval_sec)
            )

        # 6.1) Zotero 自动同步：启用时重启即增量拉取一次 + 可选定时增量拉取。
        zotero_cfg = self._config.get_zotero_sync_config()
        if zotero_cfg.enabled and zotero_cfg.auto_sync_enabled:
            self._zotero_sync_task = asyncio.create_task(
                self._periodic_zotero_sync(zotero_cfg.auto_sync_interval_sec)
            )

        # 常驻轻量检测；开关每轮重读，禁用时不读库、不推送。
        if self.source_store is not None:
            self._notion_sync_task = asyncio.create_task(self._periodic_notion_sync())

        # 6.3) Milvus 索引自动重建：常驻调度器（开关每轮重读，故不在这里按配置决定是否创建）。
        # 启动即打一次信号做「补跑」——此时 4.6 段的 _mark_all_documents_needs_reindex() 已跑完，
        # 待重建队列是终态，调度器自己查队列决定跑不跑。
        if vdb_cfg.backend == "milvus":
            from kacore.auto_reindex import AutoReindexScheduler

            # 升级后旧分块逻辑处理的文档一次性入队（Zotero 拉取结束也会再扫一次，见 api）。
            await self.api.mark_stale_processing_documents_needs_reindex()
            self.auto_reindex_scheduler = AutoReindexScheduler(
                pending_count=self.api.get_pending_reindex_count,
                run_rebuild=self.api.run_milvus_rebuild_to_completion,
                config_provider=self._config.get_vector_db_config,
                is_ready=lambda: self.vector_store is not None
                and self.embedding_provider is not None,
            )
            self.api.attach_auto_reindex_scheduler(self.auto_reindex_scheduler)
            self._auto_reindex_task = asyncio.create_task(self.auto_reindex_scheduler.run())
            self.auto_reindex_scheduler.notify()

        # 6.5) 启动摘要——记录各关键组件激活状态，方便终端页快速诊断。
        logger.info(
            "startup complete | embedding=%s dim=%s | vector_store=%s | lightrag=%s",
            self._config.get_embedding_config().provider if self._config else "N/A",
            self.embedding_dimension or "N/A",
            "Milvus" if self.vector_store is not None else "inactive",
            "on" if self.lightrag_registry is not None else "inactive",
        )
        vdb_backend = self._config.get_vector_db_config().backend if self._config else None
        if self.vector_store is None and vdb_backend == "milvus":
            logger.warning(
                "VectorStore is inactive; index rebuilds will fail until it is. Reason: %s",
                self.api.vector_store_unavailable_reason()
                if self.api is not None
                else milvus_runtime["hint"],
            )

        from kacore.legacy_import.apply import commit_applied_legacy_import
        from kacore.managers.r2_backup_manager import commit_applied_restore

        await asyncio.to_thread(commit_applied_restore, self._data_dir)
        await asyncio.to_thread(commit_applied_legacy_import, self._data_dir)
        logger.info(
            "AppInitializer.initialize() finished in %.1fs", time.monotonic() - init_started
        )

    # ── Embedding 维度探针 ──────────────────────────────────────
    async def _probe_embedding_dimension(
        self, provider: EmbeddingProvider, embedding_cfg: Any
    ) -> list[float]:
        """探一次真实向量维度，最多等 `embedding.load_timeout_seconds` 秒。

        为何不用 `asyncio.wait_for`：超时时它会取消探针任务，而本地 provider 的权重下载跑在
        `asyncio.to_thread` 的线程里、根本无法取消，取消只会丢掉「已下载一半」的进度信息。
        故改为 `asyncio.wait`——放弃等待但保留任务，完成时由回调补记日志与诊断。

        超时抛 `_EmbeddingProbeTimeout`（消息即用户可读的中文说明），由 `initialize()`
        按既有失败兜底路径降级：embedding_provider 置空、写诊断、继续把 Web 控制台拉起来。
        """
        timeout_seconds = float(getattr(embedding_cfg, "load_timeout_seconds", 0) or 0)
        if timeout_seconds <= 0:
            return await provider.embed_query(_EMBEDDING_PROBE_TEXT)

        task = asyncio.create_task(provider.embed_query(_EMBEDDING_PROBE_TEXT))
        done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
        if task in done:
            return list(task.result())

        self._pending_probe_task = task
        task.add_done_callback(self._on_late_embedding_probe)
        if str(getattr(embedding_cfg, "provider", "")) == "local":
            raise _EmbeddingProbeTimeout(
                f"Loading the local embedding model {embedding_cfg.model} timed out "
                f"({timeout_seconds:g}s): the first use downloads the weights from HuggingFace, "
                "and the download continues in the background. This start skipped vector search "
                "and the knowledge graph (keyword search still works); click “Restart service” "
                "again once it finishes."
            )
        raise _EmbeddingProbeTimeout(
            f"Embedding API probe timed out ({timeout_seconds:g}s, model={embedding_cfg.model}): "
            "check the address, network and API key, then restart the service. "
            "This start skipped vector search and the knowledge graph (keyword search still works)."
        )

    def _on_late_embedding_probe(self, task: asyncio.Task[Any]) -> None:
        """超时后仍在后台跑的探针有了结果时补记一条日志/诊断。

        用户据此知道「模型已经下完了，现在重启就能用」，而不是反复盲试。
        """
        self._pending_probe_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Background embedding probe finally failed: %s", exc)
            return
        vector = task.result() or []
        logger.info(
            (
"Background embedding probe finished (dimension %d): the model is ready; "
                    "click “Restart service” to turn on vector search."
),
            len(vector),
        )
        self._config.add_diagnostic(
"The embedding model is ready in the background; click “Restart service” to turn on "
                    "vector search and the knowledge graph."
        )

    async def _quiesce_indexes_for_backup(self) -> None:
        """完成索引落盘并释放文件锁；后续访问会按现有懒初始化契约重新打开。"""
        if self.api is not None:
            active_graph = await self.api.get_active_build_job()
            active_jobs = [
                ("LightRAG", active_graph),
                ("Milvus", self.api.get_active_milvus_build_job()),
                ("Zotero", self.api.get_active_zotero_sync_job()),
                ("document ingest", self.api.get_active_ingest_job()),
            ]
            running = [
                name
                for name, job in active_jobs
                if job is not None
                and (
                    name == "LightRAG"
                    or str(job.get("status") or "") == "running"
                )
            ]
            if running:
                raise RuntimeError(
                    "These tasks are still running; back up after they finish: "
                    f"{', '.join(running)}"
                )
        if self.lightrag_registry is not None:
            await self.lightrag_registry.close()
        if self.vector_store is not None:
            await self.vector_store.close()

    def _persist_config_value(self, section: str, key: str, value: object) -> None:
        """业务层按旧 (section, key) 写回的值（如 Notion 自动建库得到的 ID）落到对应设置键。"""
        from kacore.settings.schema import SPECS_BY_TARGET

        spec = SPECS_BY_TARGET.get((section, key))
        if spec is None:
            raise ValueError(f"runtime config key is not allowed: {section}.{key}")
        self._settings.set(spec.key, value)

    async def _periodic_backup(self, interval_sec: int) -> None:
        """周期性触发 R2 同步备份任务的后台循环。"""
        logger.info(f"Background periodic backup scheduled every {interval_sec} seconds.")
        try:
            while True:
                await asyncio.sleep(interval_sec)
                if self.r2_backup_manager is not None:
                    logger.info("Triggering periodic background backup to R2...")
                    await self.r2_backup_manager.backup(force=False)
        except asyncio.CancelledError:
            logger.info("Background periodic backup task cancelled.")
        except Exception as e:
            logger.error(f"Error in background periodic backup: {e}")

    async def _periodic_notion_sync(self) -> None:
        """10 秒合并本地变更 + 周期补偿，共用手动推送进度任务。"""
        from kacore.notion_auto_sync import NotionAutoSync

        assert self.source_store is not None
        await NotionAutoSync(
            self.source_store, self._config.get_notion_sync_config, self._auto_notion_push
        ).run()

    async def _auto_notion_push(self) -> bool | None:
        """本地维护/既有推送忙时等待，不把新变更误认为已消费。"""
        if self.api is None:
            return None
        if self.api._processing_lock.locked():
            return None
        task = self.api._notion_sync_task
        if task is not None and not task.done():
            return None
        zotero = self.api.get_active_zotero_sync_job()
        if zotero is not None and zotero.get("status") == "running":
            return None
        await self.api.sync_notion_push(force=False)
        task = self.api._notion_sync_task
        if task is None:
            return False
        await task
        job = self.api._notion_sync_job
        return job is not None and job.status == "success"

    async def _periodic_zotero_sync(self, interval_sec: int) -> None:
        """Zotero 自动同步：启动即增量拉取一次，之后每 interval_sec 再增量拉取。"""
        logger.info("Zotero auto-sync scheduled (restart + every %ss).", interval_sec)
        try:
            while True:
                if self.api is not None:
                    try:
                        await self.api.sync_zotero_pull(incremental=True)
                    except Exception as exc:
                        logger.error("Zotero auto-sync failed: %s", exc)
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("Zotero auto-sync task cancelled.")

    async def _mark_all_documents_needs_reindex(self) -> None:
        if self.source_store is None:
            return
        for doc in await self.source_store.list_documents():
            if not doc.needs_reindex:
                doc.needs_reindex = True
                try:
                    await self.source_store.update_document(doc)
                except Exception as exc:
                    logger.error("Failed to mark document %s for reindex: %s", doc.doc_id, exc)

    # ── 关闭：与构造顺序相反释放 ────────────────────────────────
    async def teardown(self) -> None:
        teardown_started = time.monotonic()
        logger.info("AppInitializer.teardown() releasing subsystems")

        if self._backup_task is not None:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._backup_task = None

        if self._zotero_sync_task is not None:
            self._zotero_sync_task.cancel()
            try:
                await self._zotero_sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._zotero_sync_task = None

        if self._notion_sync_task is not None:
            self._notion_sync_task.cancel()
            try:
                await self._notion_sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._notion_sync_task = None

        if self.api is not None and self.api._notion_sync_task is not None:
            self.api._notion_sync_task.cancel()
            await asyncio.gather(self.api._notion_sync_task, return_exceptions=True)
            self.api._notion_sync_task = None

        if self._auto_reindex_task is not None:
            self._auto_reindex_task.cancel()
            try:
                await self._auto_reindex_task
            except (asyncio.CancelledError, Exception):
                pass
            self._auto_reindex_task = None
            self.auto_reindex_scheduler = None

        # 后台探针：取消任务本身（不等待）。底层模型下载线程无法中断，会继续跑完写入
        # HuggingFace 缓存——这正是我们要的：下次 initialize 直接命中缓存。
        if self._pending_probe_task is not None:
            self._pending_probe_task.cancel()
            self._pending_probe_task = None

        # 先停止调度器和 HTTP 请求，避免取消后又产生构建；再收走独立 Milvus 任务。
        if self.api is not None:
            await self.api.cancel_build_tasks()

        if self.lightrag_registry is not None:
            try:
                await self.lightrag_registry.close()
            except Exception as e:
                logger.error(f"Failed to close LightRAG Core on teardown: {e}")
            self.lightrag_registry = None

        if self.vector_store is not None:
            try:
                await self.vector_store.close()
            except Exception as e:
                logger.error(f"Failed to close vector store on teardown: {e}")
            self.vector_store = None

        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        # 门面已不可用（数据库已关）：置空让 HTTP 层在软重启窗口内回 503 而非撞已关闭的连接。
        self.api = None
        logger.info(
            "AppInitializer.teardown() finished in %.1fs", time.monotonic() - teardown_started
        )

    async def initialize_or_rollback(self, *, raise_after_recovery: bool = True) -> None:
        """initialize()；失败时撤回本轮换入的 R2 恢复 / 旧版数据导入，再按原数据重试一次。

        没有可撤回的内容时直接上抛。撤回后重试成功：raise_after_recovery=True（软重启）仍上抛
        原异常，让调用方报告「导入 / 恢复失败」；进程薄壳首启传 False，服务照常起来，
        失败原因已写进导入结果供界面展示。
        """
        try:
            await self.initialize()
        except Exception as exc:
            from kacore.legacy_import.apply import rollback_applied_legacy_import
            from kacore.managers.r2_backup_manager import rollback_applied_restore

            rolled_back_restore = await asyncio.to_thread(
                rollback_applied_restore, self._data_dir
            )
            rolled_back_import = await asyncio.to_thread(
                rollback_applied_legacy_import, self._data_dir, error=str(exc)
            )
            if rolled_back_restore or rolled_back_import:
                logger.error(
                    "initialize() failed; rolled back the pending %s and retrying with the "
                    "original data",
                    "old plugin data import" if rolled_back_import else "R2 restore",
                    exc_info=True,
                )
                await self.teardown()
                self._settings.reload()
                self._config = Config(self._settings.to_config_raw())
                await self.initialize()
                if not raise_after_recovery:
                    return
            raise

    async def reload(self) -> None:
        """软重启：teardown 全部子系统后，重读持久化配置重建 Config 并重新 initialize。

        供「重启服务」按钮经 api.restart_service 触发；进程与 HTTP 端口不变，只在进程内
        重建全部子系统。重启期间到达的请求可能短暂失败，由前端轮询重连。
        """
        reload_started = time.monotonic()
        logger.info("AppInitializer.reload() soft restart triggered")
        await self.teardown()
        self._settings.reload()
        self._config = Config(self._settings.to_config_raw())
        try:
            await self.initialize_or_rollback()
        except Exception:
            logger.error(
                "The initialize() step of the soft restart failed (%.1fs)",
                time.monotonic() - reload_started,
                exc_info=True,
            )
            raise
        logger.info(
            "AppInitializer.reload() soft restart finished in %.1fs",
            time.monotonic() - reload_started,
        )


__all__ = ["AppInitializer"]
