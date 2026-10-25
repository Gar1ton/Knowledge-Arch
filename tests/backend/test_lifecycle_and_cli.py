"""单元与集成测试：AppInitializer 生命周期与后台任务调度。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import fitz  # type: ignore[import-untyped]
import pytest

from kacore.app_initializer import AppInitializer
from kacore.migration_runner import MIGRATIONS_DIR, run_migrations


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def raw_config() -> dict[str, Any]:
    return {
        "source_store": {
            "db_filename": "test_kr.db",
            "default_collection": "default",
            "ocr_enabled": False,
        },
        "r2_sync": {
            "enabled": True,
            "account_id": "test-acc",
            "access_key_id": "test-key",
            "secret_access_key": "test-sec",
            "bucket": "test-bucket",
            "backup_interval_sec": 60,
        },
        "notion_sync": {
            "enabled": True,
            "parent_page_id": "parent-page",
            "database_title": "KR Test",
            "rate_limit_rps": 100,
        },
        # Lexical-only retrieval avoids loading a local embedding model in lifecycle
        # tests that do not exercise Milvus.
        "vector_db": {"backend": "lexical"},
    }


async def test_app_initializer_lifecycle(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    # 1) 实例化 AppInitializer
    initializer = AppInitializer(raw_config, temp_dir)
    assert initializer.source_store is None
    assert initializer._backup_task is None

    # 2) 初始化
    await initializer.initialize()

    assert initializer.source_store is not None
    assert initializer.api is not None
    assert initializer.ingest_manager is not None
    assert initializer.category_manager is not None
    assert initializer.quota_manager is not None
    assert initializer.sync_pipeline is not None
    assert initializer._backup_task is not None
    assert not initializer._backup_task.done()

    # 3) 销毁
    await initializer.teardown()
    assert initializer._backup_task is None


def test_initializer_uses_project_migration_runner() -> None:
    assert run_migrations.__module__ == "kacore.migration_runner"
    assert MIGRATIONS_DIR.name == "migrations"
    assert (MIGRATIONS_DIR / "001_source_store.sql").is_file()


async def test_app_initializer_lifecycle_periodic_disabled(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    # 禁用 R2 或周期备份
    config_disabled = dict(raw_config)
    config_disabled["r2_sync"] = dict(raw_config["r2_sync"])
    config_disabled["r2_sync"]["enabled"] = False

    initializer = AppInitializer(config_disabled, temp_dir)
    await initializer.initialize()
    assert initializer._backup_task is None
    await initializer.teardown()


async def test_initializer_creates_default_collection_without_overwriting_it(
    temp_dir: Path
) -> None:
    from kacore.domain.models import Collection

    config = {"vector_db": {"backend": "lexical"}}
    first = AppInitializer(config, temp_dir)
    await first.initialize()
    assert first.source_store is not None
    await first.source_store.upsert_collection(
        Collection(name="default", description="User description")
    )
    await first.teardown()

    second = AppInitializer(config, temp_dir)
    await second.initialize()
    assert second.source_store is not None
    collections = await second.source_store.list_collections()
    assert [(item.name, item.description) for item in collections] == [
        ("default", "User description")
    ]
    await second.teardown()


async def test_initializer_passes_probed_dimension_to_milvus_and_lightrag(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    captured: dict[str, int] = {}

    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 7

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 7 for _ in texts]

    class Milvus:
        def __init__(self, *, db_path: str, dim: int) -> None:
            captured["milvus"] = dim

        def validate_schema(self) -> None:
            pass

        async def close(self) -> None:
            pass

    class Registry:
        def __init__(self, **kwargs: Any) -> None:
            captured["lightrag"] = kwargs["embedding_dim"]

        def existing_collections(self) -> list[str]:
            return []

        async def close(self) -> None:
            pass

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": True},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.repository.vector_store.milvus_lite.MilvusLiteVectorStore", Milvus),
        patch("kacore.lightrag_core.LightRAGCoreRegistry", Registry),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()
        assert initializer.embedding_dimension == 7
        assert initializer.config.runtime_embedding_dimension == 7
        assert captured == {"milvus": 7, "lightrag": 7}
        assert initializer.api is not None
        await initializer.teardown()


async def test_default_initializer_starts_without_optional_feature_packages(
    temp_dir: Path
) -> None:
    blocked_modules = {
        "boto3": None,
        "botocore": None,
        "lightrag": None,
        "pymilvus": None,
        "sentence_transformers": None,
    }
    with patch.dict(sys.modules, blocked_modules):
        initializer = AppInitializer({}, temp_dir)
        await initializer.initialize()

        assert initializer.api is not None
        assert initializer.source_store is not None
        assert initializer.vector_store is None
        assert initializer.lightrag_registry is None
        diagnostics = initializer.config.get_diagnostics()
        # 可选特性缺包仍给出可执行指引：local embedding 指向 requirements-additional.txt；
        # Milvus Lite 自 v0.24.6 起为必装依赖，缺失时改提示 requirements.txt + SQLite 词法兜底。
        assert any("requirements-additional.txt" in item for item in diagnostics)
        assert any(
            "Milvus Lite is a required dependency from requirements.txt" in item
            for item in diagnostics
        )
        await initializer.teardown()


async def test_initializer_probe_failure_disables_embedding_indexes(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("probe failed")

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": True},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()
        assert initializer.embedding_provider is None
        assert initializer.vector_store is None
        assert initializer.lightrag_registry is None
        assert any(
            "Embedding dimension probe failed" in item
            for item in initializer.config.get_diagnostics()
        )
        await initializer.teardown()


async def test_initializer_probe_timeout_keeps_startup_moving(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    """探针挂死（如 HuggingFace 下载卡住）不得拖住 initialize()。

    回归 v1.0.9：重启会先 teardown 掉旧 Web 控制台，若探针无限等待，新控制台永远起不来，
    数据流页表现为「重启卡死」。这里断言：超时后 initialize() 正常走完、写中文诊断、
    并保留后台探针任务（不取消——模型下载线程无法中断，留着让它写满缓存）。
    """
    import asyncio

    class HangingProvider:
        async def embed_query(self, text: str) -> list[float]:
            await asyncio.sleep(30)
            return [0.1]

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "embedding": {"provider": "local", "load_timeout_seconds": 1},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=HangingProvider(),
        ),
        patch("kacore.app_initializer._module_available", return_value=True),
        # 生产下限 30s 是防用户把正常加载误判为卡死；测试只关心「超时后还能走完」。
        patch("kacore.config._LOAD_TIMEOUT_MIN_SECONDS", 1),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()

        # 走完了：embedding 降级、后续装配（含 Web 控制台阶段）未被阻断。
        assert initializer.embedding_provider is None
        assert initializer.vector_store is None
        assert initializer.api is not None
        diagnostics = initializer.config.get_diagnostics()
        assert any("加载超时" in item and "重启服务" in item for item in diagnostics)
        # 探针任务留在后台（下载继续），teardown 才收走。
        assert initializer._pending_probe_task is not None
        await initializer.teardown()
        assert initializer._pending_probe_task is None


async def test_fresh_install_uploads_and_lexically_retrieves_when_embedding_probe_fails(
    temp_dir: Path
) -> None:
    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("offline first startup")

    pdf_path = temp_dir / "fresh-install.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 550, 750),
        "Fresh install lexical baseline remains available without embeddings.",
    )
    pdf.save(pdf_path)
    pdf.close()

    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer({}, temp_dir)
        await initializer.initialize()

        assert initializer.source_store is not None
        assert initializer.api is not None
        assert initializer.retrieval_orchestrator is not None
        assert [item.name for item in await initializer.source_store.list_collections()] == [
            "default"
        ]

        doc_id = await initializer.api.register_document(
            title=pdf_path.name,
            file_path=str(pdf_path),
            content_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            content_hash="",
            collection="default",
        )
        stored = await initializer.source_store.get_document(doc_id)
        assert stored is not None
        assert Path(stored.file_path) == temp_dir / "library" / doc_id / "original.pdf"
        assert Path(stored.file_path).exists()

        result = await initializer.retrieval_orchestrator.retrieve_with_outcome(
            "default", "lexical baseline", 5
        )
        assert [chunk.doc_id for chunk in result.chunks] == [doc_id]
        assert "sqlite_lexical" in result.engines
        assert result.fallback_reason == "milvus_unavailable"
        await initializer.teardown()


async def test_initializer_keeps_mismatched_milvus_available_for_manual_rebuild(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    from kacore.repository.vector_store.milvus_lite import MilvusSchemaMismatchError

    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 7

    class Milvus:
        def __init__(self, *, db_path: str, dim: int) -> None:
            self.dim = dim

        def validate_schema(self) -> None:
            raise MilvusSchemaMismatchError("rebuild required")

        async def close(self) -> None:
            pass

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": False},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.repository.vector_store.milvus_lite.MilvusLiteVectorStore", Milvus),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()

        assert isinstance(initializer.vector_store, Milvus)
        assert initializer.embedding_fingerprint is not None
        assert initializer.index_compatibility is not None
        assert not initializer.index_compatibility.is_milvus_compatible(
            initializer.embedding_fingerprint
        )
        assert "rebuild required" in initializer.index_compatibility.reason("milvus")
        await initializer.teardown()


async def test_initializer_records_milvus_init_error_for_generic_failure(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    """通用装配异常（如 DataDirLockedError）必须被记录，喂给 capabilities 面板与清锁守卫。"""

    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 7

    class Milvus:
        def __init__(self, *, db_path: str, dim: int) -> None:
            pass

        def validate_schema(self) -> None:
            raise RuntimeError(
                "DataDirLockedError: another process holds the lock on "
                "'/data/vector_store.db': [Errno 13] Permission denied"
            )

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": False},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.repository.vector_store.milvus_lite.MilvusLiteVectorStore", Milvus),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()

        assert initializer.vector_store is None
        assert initializer._vector_store_init_error is not None
        assert "another process holds the lock" in initializer._vector_store_init_error
        # 组合根必须把它接到 api，供 capabilities 面板展示。
        assert initializer.api is not None
        assert initializer.api._vector_store_init_error == initializer._vector_store_init_error
        await initializer.teardown()


async def test_clear_stale_milvus_lock_rejects_when_not_lock_issue(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    """向导护栏：没有检测到锁定问题时拒绝清除（不同的失败原因不该被这个按钮处理）。"""
    config = {**raw_config, "vector_db": {"backend": "milvus"}, "graph": {"enabled": False}}
    initializer = AppInitializer(config, temp_dir)
    initializer.vector_store = None
    initializer._vector_store_init_error = "some unrelated schema error"

    result = await initializer.clear_stale_milvus_lock()

    assert result["status"] == "error"
    assert "未检测到" in result["message"]


async def test_clear_stale_milvus_lock_rejects_when_vector_store_ready(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    """守卫：vector_store 已经装配成功时不该允许清锁（那把锁此时是正当持有）。"""
    config = {**raw_config, "vector_db": {"backend": "milvus"}, "graph": {"enabled": False}}
    initializer = AppInitializer(config, temp_dir)
    initializer.vector_store = object()  # 假装已装配成功
    initializer._vector_store_init_error = None

    result = await initializer.clear_stale_milvus_lock()

    assert result["status"] == "error"
    assert "无需清除" in result["message"]


async def test_clear_stale_milvus_lock_refuses_to_delete_os_lock_file(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    """空 LOCK 仍可能被当前宿主持有；不能靠删除文件绕过 OS 锁。"""
    config = {**raw_config, "vector_db": {"backend": "milvus"}, "graph": {"enabled": False}}
    initializer = AppInitializer(config, temp_dir)
    initializer.vector_store = None
    initializer._vector_store_init_error = (
        "DataDirLockedError: another process holds the lock on "
        "'/data/vector_store.db': [Errno 13] Permission denied"
    )
    db_dir = temp_dir / "vector_store.db"
    db_dir.mkdir(parents=True)
    (db_dir / "LOCK").write_bytes(b"")
    collections_dir = db_dir / "collections"
    collections_dir.mkdir()
    (collections_dir / "kb_chunks.marker").write_text("real data, do not touch")
    initializer._milvus_db_path = db_dir

    result = await initializer.clear_stale_milvus_lock()

    assert result["status"] == "error"
    assert "完整退出应用" in result["message"]
    assert (db_dir / "LOCK").exists()
    assert (collections_dir / "kb_chunks.marker").read_text() == "real data, do not touch"
    assert initializer._vector_store_init_error is not None


async def test_initializer_marks_recreated_empty_milvus_incompatible_when_docs_exist(
    temp_dir: Path, raw_config: dict[str, Any]
) -> None:
    from kacore.domain.models import Collection, SourceDocument

    seed_config = {
        **raw_config,
        "vector_db": {"backend": "lexical"},
        "graph": {"enabled": False},
    }
    seed = AppInitializer(seed_config, temp_dir)
    await seed.initialize()
    assert seed.source_store is not None
    await seed.source_store.upsert_collection(Collection(name="papers"))
    await seed.source_store.add_document(
        SourceDocument("d1", "Doc", "/d1.pdf", "application/pdf", 1, "h", "papers")
    )
    await seed.teardown()

    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 7

    class Milvus:
        created_collection = True

        def __init__(self, *, db_path: str, dim: int) -> None:
            pass

        def validate_schema(self) -> None:
            pass

        async def close(self) -> None:
            pass

    config = {
        **raw_config,
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": False},
    }
    with (
        patch(
            "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
            return_value=Provider(),
        ),
        patch("kacore.repository.vector_store.milvus_lite.MilvusLiteVectorStore", Milvus),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        initializer = AppInitializer(config, temp_dir)
        await initializer.initialize()

        assert initializer.embedding_fingerprint is not None
        assert initializer.index_compatibility is not None
        assert not initializer.index_compatibility.is_milvus_compatible(
            initializer.embedding_fingerprint
        )
        assert "recreated" in initializer.index_compatibility.reason("milvus")
        assert initializer.source_store is not None
        assert [
            doc.doc_id
            for doc in await initializer.source_store.list_pending_reindex_documents()
        ] == ["d1"]
        await initializer.teardown()


async def test_notion_auto_push_waits_for_local_processing_and_inflight_push(
    tmp_path: Path,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from kacore.utils.processing_lock import ProcessingLock

    initializer = AppInitializer({}, tmp_path)
    lock = ProcessingLock()
    push = AsyncMock()
    initializer.api = SimpleNamespace(
        _processing_lock=lock,
        _notion_sync_task=None,
        get_active_zotero_sync_job=lambda: None,
        sync_notion_push=push,
    )
    async with lock:
        assert await initializer._auto_notion_push() is None
    task = asyncio.create_task(asyncio.Event().wait())
    initializer.api._notion_sync_task = task
    try:
        assert await initializer._auto_notion_push() is None
        push.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_notion_teardown_cancels_scheduler_and_pending_push_before_store_close(
    tmp_path: Path,
) -> None:
    import asyncio

    initializer = AppInitializer({"vector_db": {"backend": "lexical"}}, tmp_path)
    await initializer.initialize()
    scheduler = initializer._notion_sync_task
    assert scheduler is not None
    ended = asyncio.Event()
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    push = asyncio.create_task(work())
    initializer.api._notion_sync_task = push
    await started.wait()
    await initializer.teardown()
    assert scheduler.done() and push.done()
    assert ended.is_set()
    assert initializer._notion_sync_task is None
