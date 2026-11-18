"""Milvus 向量索引：配置解析、HNSW 参数、已有集合的后台换索引与检索排队（P30）。"""
from __future__ import annotations

import asyncio
import importlib.util
import random
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from settings_helpers import legacy_settings

from kacore.app_initializer import AppInitializer
from kacore.config import Config, VectorDbConfig
from kacore.domain.models import DocumentChunk
from kacore.repository.vector_store.milvus_lite import (
    INDEX_FLAT,
    INDEX_HNSW,
    MilvusIndexSpec,
    MilvusLiteVectorStore,
)

_REAL_MILVUS = all(importlib.util.find_spec(name) is not None
                   for name in ("pymilvus", "milvus_lite", "faiss"))


# ── Config ──────────────────────────────────────────────────────

def test_vector_index_defaults_to_hnsw() -> None:
    vdb = Config({}).get_vector_db_config()
    assert vdb.index_type == INDEX_HNSW
    assert (vdb.hnsw_m, vdb.hnsw_ef_construction, vdb.hnsw_ef_search) == (16, 200, 64)
    assert vdb.search_concurrency == VectorDbConfig.search_concurrency


def test_vector_index_config_normalises_and_clamps() -> None:
    vdb = Config({"vector_db": {
        "index_type": "flat", "hnsw_m": 1000, "hnsw_ef_search": "x", "search_concurrency": 0,
    }}).get_vector_db_config()
    assert vdb.index_type == INDEX_FLAT
    assert vdb.hnsw_m == 64
    assert vdb.hnsw_ef_search == VectorDbConfig.hnsw_ef_search
    assert vdb.search_concurrency == 1
    unknown = Config({"vector_db": {"index_type": "IVF_PQ"}}).get_vector_db_config()
    assert unknown.index_type == INDEX_HNSW


def test_index_spec_search_params_follow_active_index() -> None:
    spec = MilvusIndexSpec(hnsw_ef_search=80)
    assert spec.build_params() == {"M": 16, "efConstruction": 200}
    assert spec.search_params(INDEX_HNSW) == {"params": {"ef": 80}}
    # 迁移完成前集合仍是 FLAT：不能把 HNSW 专属参数传给它。
    assert spec.search_params(INDEX_FLAT) is None
    assert MilvusIndexSpec(index_type=INDEX_FLAT).build_params() == {}


# ── Existing collections (mock client) ──────────────────────────

def _store_with_client(tmp_path: Path, index_type: str | None) -> tuple[Any, MagicMock]:
    client = MagicMock()
    state = {"index": index_type}
    client.has_collection.return_value = True
    client.describe_collection.return_value = {"fields": [{"name": "vector", "params": {"dim": 4}}]}
    client.list_indexes.side_effect = lambda _c: ["vector"] if state["index"] else []
    client.describe_index.side_effect = lambda _c, _f: {"index_type": state["index"]}
    client.drop_index.side_effect = lambda *_a: state.update(index=None)
    client.create_index.side_effect = lambda *_a: state.update(index=INDEX_HNSW)
    client.search.return_value = [[{"id": "c1", "distance": 1.0}]]
    store = MilvusLiteVectorStore(str(tmp_path / "v.db"), dim=4)
    store._service = SimpleNamespace(open_client=lambda: client, release=lambda: None)
    return store, client


def test_existing_flat_collection_is_marked_not_migrated_on_open(tmp_path: Path) -> None:
    store, client = _store_with_client(tmp_path, INDEX_FLAT)
    store.validate_schema()
    assert store.index_migration_pending
    assert store._active_index_type == INDEX_FLAT
    client.create_index.assert_not_called()
    client.drop_index.assert_not_called()


def test_collection_without_index_gets_one_before_load(tmp_path: Path) -> None:
    store, client = _store_with_client(tmp_path, None)
    store.validate_schema()
    client.create_index.assert_called_once()
    assert not store.index_migration_pending
    assert store._active_index_type == INDEX_HNSW


async def test_flat_search_omits_hnsw_params(tmp_path: Path) -> None:
    store, client = _store_with_client(tmp_path, INDEX_FLAT)
    store.validate_schema()
    await store.search("default", [1.0, 0.0, 0.0, 0.0], 3)
    assert "search_params" not in client.search.call_args.kwargs


async def test_migrate_index_switches_and_queues_searches(tmp_path: Path) -> None:
    store, client = _store_with_client(tmp_path, INDEX_FLAT)
    store.validate_schema()
    events: list[str] = []
    in_migration = threading.Event()
    release_migration = threading.Event()

    def release(_c: str) -> None:
        events.append("release")
        in_migration.set()
        release_migration.wait(5)

    client.release_collection.side_effect = release
    client.load_collection.side_effect = lambda _c: events.append("load")
    client.search.side_effect = lambda **_k: events.append("search") or [
        [{"id": "c1", "distance": 1.0}]
    ]

    migration = asyncio.create_task(store.migrate_index())
    await asyncio.to_thread(in_migration.wait, 5)
    search = asyncio.create_task(store.search("default", [1.0, 0.0, 0.0, 0.0], 1))
    await asyncio.sleep(0.05)
    assert not search.done()  # 换索引期间检索排队，不报错
    release_migration.set()
    assert await migration is True
    assert await search == [("c1", 1.0)]
    assert events == ["release", "load", "search"]
    assert not store.index_migration_pending
    assert client.search.call_args.kwargs["search_params"] == {"params": {"ef": 64}}
    assert await store.migrate_index() is False  # 只换一次


async def test_failed_migration_still_reloads_collection(tmp_path: Path) -> None:
    store, client = _store_with_client(tmp_path, INDEX_FLAT)
    store.validate_schema()
    client.load_collection.reset_mock()
    client.create_index.side_effect = [RuntimeError("faiss missing"), None]
    with pytest.raises(RuntimeError, match="faiss missing"):
        await store.migrate_index()
    client.load_collection.assert_called_once()
    assert store._initialized  # 之后的检索不会卡在排队


# ── Composition root ────────────────────────────────────────────

async def test_teardown_finishes_index_switch_before_closing_store(tmp_path: Path) -> None:
    events: list[str] = []
    started = asyncio.Event()

    async def migrate() -> bool:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("migration_stopped")
        return True

    initializer = AppInitializer(legacy_settings({}), tmp_path)
    initializer.vector_store = SimpleNamespace(
        migrate_index=migrate,
        close=AsyncMock(side_effect=lambda: events.append("store_closed")),
    )
    initializer._vector_index_task = asyncio.create_task(initializer._migrate_vector_index())
    await started.wait()
    await initializer.teardown()
    assert events == ["migration_stopped", "store_closed"]
    assert initializer._vector_index_task is None


# ── Real Milvus Lite ────────────────────────────────────────────

@pytest.mark.skipif(not _REAL_MILVUS, reason="需要 Milvus Lite + faiss 集成依赖")
async def test_real_flat_collection_switches_to_hnsw_and_keeps_results(tmp_path: Path) -> None:
    path = str(tmp_path / "vectors.db")
    chunks = [DocumentChunk(f"c{i}", f"d{i}", 0, f"text {i}", f"h{i}") for i in range(40)]
    rng = random.Random(7)  # 互不相同的向量：并列名次的先后本来就不确定，不能拿来比
    vectors = [[rng.uniform(-1.0, 1.0) for _ in range(4)] for _ in range(40)]
    query = vectors[7]

    flat = MilvusLiteVectorStore(path, dim=4, index=MilvusIndexSpec(index_type=INDEX_FLAT))
    try:
        await flat.upsert_chunks(chunks, vectors)
        expected = await flat.search("default", query, 5)
    finally:
        await flat.close()

    store = MilvusLiteVectorStore(path, dim=4)
    try:
        store.validate_schema()
        assert store.index_migration_pending
        before = await store.search("default", query, 5)  # 迁移前照常检索
        assert [cid for cid, _ in before] == [cid for cid, _ in expected]
        assert await store.migrate_index() is True
        after = await store.search("default", query, 5)
        assert after[0][0] == expected[0][0]
        assert {cid for cid, _ in after} == {cid for cid, _ in expected}
    finally:
        await store.close()

    reopened = MilvusLiteVectorStore(path, dim=4)
    try:
        reopened.validate_schema()
        assert not reopened.index_migration_pending
        assert reopened._active_index_type == INDEX_HNSW
    finally:
        await reopened.close()
