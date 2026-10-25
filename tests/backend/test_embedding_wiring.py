"""Embedding 接线：配置归一、缓存 namespace、索引指纹、工厂、API 重建标记、组合根。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kacore.api import KnowledgeRepositoryApi
from kacore.app_initializer import AppInitializer
from kacore.capabilities import detect_pipeline
from kacore.config import Config, EmbeddingConfig
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.index_compatibility import IndexCompatibilityStore, embedding_fingerprint
from kacore.repository.embedding.cached import CachedEmbeddingProvider
from kacore.repository.embedding.external import ExternalEmbeddingProvider
from kacore.repository.embedding.factory import EmbeddingProviderFactory
from kacore.repository.embedding.local import LocalEmbeddingProvider
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from tests.backend.test_embedding import MockEmbeddingProvider

# ── 配置 ─────────────────────────────────────────────────────────


def test_describe_embedding_keeps_legacy_format_for_local_and_external() -> None:
    assert Config({}).describe_embedding() == "local:intfloat/multilingual-e5-small"
    assert (
        Config({"embedding": {"provider": "external", "model": "m"}}).describe_embedding()
        == "external:m"
    )


@pytest.mark.parametrize("legacy", ["astr", "ASTR", "bogus", ""])
def test_removed_or_unknown_embedding_sources_normalize_to_local(legacy: str) -> None:
    cfg = Config({"embedding": {"provider": legacy, "model": "m"}}).get_embedding_config()
    assert cfg.provider == "local"


def test_pipeline_snapshot_lists_only_local_and_external_sources() -> None:
    stages = {stage["id"]: stage for stage in detect_pipeline(Config({}))}
    assert stages["embedding"]["candidates"] == ["local", "external"]
    assert stages["vector_store"]["candidates"] == ["milvus", "lexical"]


@pytest.mark.parametrize("legacy", ["astr", " Astr "])
def test_legacy_knowledge_base_backend_normalizes_to_lexical(legacy: str) -> None:
    assert Config({"vector_db": {"backend": legacy}}).get_vector_db_config().backend == "lexical"


# ── 缓存 namespace ───────────────────────────────────────────────


def test_cache_namespace_of_providers_is_byte_identical_to_before() -> None:
    cached = CachedEmbeddingProvider(MockEmbeddingProvider(), namespace="external:m:https://x")

    assert cached.identity == {}
    assert cached._get_hash("t") == hashlib.sha256(b"external:m:https://x\0t").hexdigest()


# ── 索引指纹 ─────────────────────────────────────────────────────


def test_local_and_external_fingerprints_are_frozen_against_previous_values() -> None:
    """升级不得让既有 local/external 索引被误判为不兼容：值取自插件版实现。"""
    local = EmbeddingConfig(
        provider="local", model="intfloat/multilingual-e5-small", base_url="https://api.openai.com/v1"
    )
    external = EmbeddingConfig(
        provider="external", model="text-embedding-3-small", base_url="https://api.example.test/v1"
    )

    assert embedding_fingerprint(local, 384) == (
        "901fa583ba316a6ba3174b92a009840be8e4708296a12de9b23e8dba41a5351e"
    )
    assert embedding_fingerprint(external, 1536) == (
        "70a10d12bb9494ebd1dacea230f7d594fb38f1eb14c8a6e6f4c0c30db3166304"
    )


def test_fingerprint_changes_with_provider_model_url_and_dimension() -> None:
    base = EmbeddingConfig(provider="external", model="m", base_url="https://x/v1")
    variants = [
        EmbeddingConfig(provider="local", model="m", base_url="https://x/v1"),
        EmbeddingConfig(provider="external", model="m2", base_url="https://x/v1"),
        EmbeddingConfig(provider="external", model="m", base_url="https://y/v1"),
    ]
    reference = embedding_fingerprint(base, 8)
    assert all(embedding_fingerprint(v, 8) != reference for v in variants)
    assert embedding_fingerprint(base, 16) != reference


# ── 工厂 ─────────────────────────────────────────────────────────


def test_factory_builds_cached_local_and_external_providers(tmp_path: Path) -> None:
    local = EmbeddingProviderFactory.create_provider(
        Config({"embedding": {"provider": "local"}}), db_dir=str(tmp_path)
    )
    external = EmbeddingProviderFactory.create_provider(
        Config({"embedding": {"provider": "external", "model": "m", "base_url": "https://x/v1"}}),
        db_dir=str(tmp_path),
    )

    assert isinstance(local._inner, LocalEmbeddingProvider)  # type: ignore[attr-defined]
    assert isinstance(external._inner, ExternalEmbeddingProvider)  # type: ignore[attr-defined]
    assert local._namespace.startswith("local:")  # type: ignore[attr-defined]
    assert external._namespace == "external:m:https://x/v1"  # type: ignore[attr-defined]


# ── API：改 provider → 索引标记重建 ─────────────────────────────


async def _api_with_compatible_index(tmp_path: Path, raw: dict[str, Any]) -> tuple[Any, ...]:
    from kacore.repository.vector_store.memory import InMemoryVectorStore

    store = InMemorySourceDocumentStore()
    await store.add_document(
        SourceDocument("d1", "d1", "/d1.pdf", "application/pdf", 1, "h", "papers")
    )
    await store.replace_chunks("d1", [DocumentChunk("c1", "d1", 0, "evidence", "h1")])
    compat = IndexCompatibilityStore(tmp_path / "compat.json")
    compat.mark_milvus_compatible("fp")
    api = KnowledgeRepositoryApi(
        source_store=store,
        config=Config(raw),
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(dimension=4),
        index_compatibility=compat,
        embedding_fingerprint="fp",
    )
    return api, store, compat


async def test_switching_embedding_provider_marks_indexes_for_rebuild(tmp_path: Path) -> None:
    api, store, compat = await _api_with_compatible_index(
        tmp_path, {"vector_db": {"backend": "milvus"}, "embedding": {"provider": "local"}}
    )

    switched = await api.update_config_value("embedding", "provider", "external")

    assert switched["rebuild_required"] is True and switched["restart_required"] is True
    assert not compat.is_milvus_compatible("fp")
    assert [d.doc_id for d in await store.list_pending_reindex_documents()] == ["d1"]


async def test_resaving_the_same_provider_does_not_invalidate_indexes(tmp_path: Path) -> None:
    api, _store, compat = await _api_with_compatible_index(
        tmp_path, {"vector_db": {"backend": "milvus"}, "embedding": {"provider": "external"}}
    )

    same = await api.update_config_value("embedding", "provider", "external")

    assert same["rebuild_required"] is False and same["restart_required"] is False
    assert compat.is_milvus_compatible("fp")


@pytest.mark.parametrize("bad", ["astr", "bogus", 123])
async def test_removed_embedding_sources_are_rejected_by_the_api(tmp_path: Path, bad: Any) -> None:
    api, _store, compat = await _api_with_compatible_index(
        tmp_path, {"vector_db": {"backend": "milvus"}}
    )
    with pytest.raises(ValueError, match="'local' or 'external'"):
        await api.update_config_value("embedding", "provider", bad)
    assert compat.is_milvus_compatible("fp")


# ── 组合根 ───────────────────────────────────────────────────────


class _Milvus:
    dims: list[int] = []
    created_collection = True

    def __init__(self, *, db_path: str, dim: int) -> None:
        type(self).dims.append(dim)

    def validate_schema(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def milvus_stub():
    _Milvus.dims = []
    _Milvus.created_collection = True
    with (
        patch("kacore.repository.vector_store.milvus_lite.MilvusLiteVectorStore", _Milvus),
        patch("kacore.app_initializer._module_available", return_value=True),
    ):
        yield _Milvus


async def test_startup_probes_dimension_and_builds_fingerprint(
    tmp_path: Path, milvus_stub: type[_Milvus]
) -> None:
    class Provider:  # 没有 identity 属性的旧式桩
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 7

    raw = {"vector_db": {"backend": "milvus"}, "graph": {"enabled": False}}
    with patch(
        "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
        return_value=Provider(),
    ):
        initializer = AppInitializer(raw, tmp_path)
        await initializer.initialize()
        try:
            assert initializer.embedding_dimension == 7
            assert initializer.embedding_fingerprint == embedding_fingerprint(
                initializer.config.get_embedding_config(), 7
            )
            assert milvus_stub.dims == [7]
        finally:
            await initializer.teardown()


async def test_legacy_embedding_source_flags_existing_index_for_rebuild(
    tmp_path: Path, milvus_stub: type[_Milvus]
) -> None:
    """旧库用已移除的 embedding 来源建索引：启动后指纹不匹配，走既有「不兼容 → 重建」。"""

    class Provider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 4

    IndexCompatibilityStore(tmp_path / "index_compatibility.json").mark_milvus_compatible(
        "fingerprint-built-with-a-removed-source"
    )
    milvus_stub.created_collection = False
    raw = {
        "vector_db": {"backend": "milvus"},
        "graph": {"enabled": False},
        "embedding": {"provider": "astr"},
    }
    with patch(
        "kacore.repository.embedding.factory.EmbeddingProviderFactory.create_provider",
        return_value=Provider(),
    ):
        initializer = AppInitializer(raw, tmp_path)
        await initializer.initialize()
        try:
            assert initializer.config.get_embedding_config().provider == "local"
            assert any(
                "Milvus index is incompatible with the active embedding" in d
                for d in initializer.config.get_diagnostics()
            )
        finally:
            await initializer.teardown()
