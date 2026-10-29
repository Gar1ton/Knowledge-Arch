"""作用域检索测试：resolve_scope（item/collection 后代/tag/library）+ 跨通道硬过滤 + 图谱越界。"""
from __future__ import annotations

import pytest

from kacore.config import Config
from kacore.domain.models import (
    Collection,
    DocumentChunk,
    DocumentLifecycle,
    DocumentOrigin,
    SourceDocument,
    ZoteroCollection,
    ZoteroTag,
)
from kacore.index_compatibility import IndexCompatibilityStore
from kacore.pipelines.retrieval_orchestrator import (
    SCOPE_COLLECTION,
    SCOPE_DOCUMENT,
    SCOPE_ITEM,
    SCOPE_LIBRARY,
    SCOPE_TAG,
    RetrievalOrchestrator,
    RetrievalScope,
)
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

LIB = "1"


def _doc(doc_id: str, item_key: str, *, lifecycle=DocumentLifecycle.ACTIVE) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        title=doc_id,
        file_path=f"/{doc_id}.pdf",
        content_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        collection="kb",
        library_id=LIB,
        zotero_item_key=item_key,
        origin=DocumentOrigin.ZOTERO,
        lifecycle_state=lifecycle,
    )


async def _store_with_mirror() -> InMemorySourceDocumentStore:
    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="kb"))
    # 集合树 root -> child；item A 在 root，item B 在 child，item C 无集合但有 tag
    await store.upsert_zotero_collection(
        ZoteroCollection(collection_key="root", library_id=LIB, name="Root")
    )
    await store.upsert_zotero_collection(
        ZoteroCollection(
            collection_key="child", library_id=LIB, name="Child", parent_collection_key="root"
        )
    )
    await store.set_item_collections(LIB, "ITEMA", ["root"])
    await store.set_item_collections(LIB, "ITEMB", ["child"])
    await store.replace_item_tags(LIB, "ITEMC", [ZoteroTag("ITEMC", "shared", 0)])
    await store.replace_item_tags(LIB, "ITEMA", [ZoteroTag("ITEMA", "shared", 0)])
    # 统一 collections 树（coll_key 复用 root/child）+ 多归属（collection scope 走此路径）。
    await store.upsert_collection(
        Collection(name="Root", coll_key="root", origin=DocumentOrigin.ZOTERO, library_id=LIB)
    )
    await store.upsert_collection(
        Collection(
            name="Child", coll_key="child", parent_key="root",
            origin=DocumentOrigin.ZOTERO, library_id=LIB,
        )
    )
    for doc_id, item in [("dA", "ITEMA"), ("dB", "ITEMB"), ("dC", "ITEMC")]:
        await store.add_document(_doc(doc_id, item))
    await store.set_document_collections("dA", ["root"])
    await store.set_document_collections("dB", ["child"])
    return store


def _orch(store: InMemorySourceDocumentStore) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        source_store=store, config=Config({"vector_db": {"backend": "lexical"}})
    )


# ── resolve_scope ────────────────────────────────────────────────


async def test_resolve_scope_item() -> None:
    store = await _store_with_mirror()
    orch = _orch(store)
    allowed = await orch.resolve_scope(RetrievalScope(SCOPE_ITEM, "ITEMA", LIB))
    assert allowed == {"dA"}


async def test_resolve_scope_collection_includes_descendants() -> None:
    store = await _store_with_mirror()
    orch = _orch(store)
    allowed = await orch.resolve_scope(RetrievalScope(SCOPE_COLLECTION, "root", LIB))
    assert allowed == {"dA", "dB"}  # root + child 后代


async def test_resolve_scope_tag() -> None:
    store = await _store_with_mirror()
    orch = _orch(store)
    allowed = await orch.resolve_scope(RetrievalScope(SCOPE_TAG, "shared", LIB))
    assert allowed == {"dA", "dC"}


async def test_resolve_scope_library_excludes_detached() -> None:
    store = await _store_with_mirror()
    detached = _doc("dD", "ITEMD", lifecycle=DocumentLifecycle.DETACHED)
    await store.add_document(detached)
    orch = _orch(store)
    allowed = await orch.resolve_scope(RetrievalScope(SCOPE_LIBRARY, LIB, LIB))
    assert allowed == {"dA", "dB", "dC"}  # 排除 detached dD


async def test_resolve_scope_document_is_that_doc_only() -> None:
    store = await _store_with_mirror()
    await store.add_document(_doc("dD", "ITEMD", lifecycle=DocumentLifecycle.DETACHED))
    orch = _orch(store)
    assert await orch.resolve_scope(RetrievalScope(SCOPE_DOCUMENT, "dB", LIB)) == {"dB"}
    # 已脱管或不存在的文档：作用域为空（不放行任何东西），而不是退回不过滤。
    assert await orch.resolve_scope(RetrievalScope(SCOPE_DOCUMENT, "dD", LIB)) == set()
    assert await orch.resolve_scope(RetrievalScope(SCOPE_DOCUMENT, "nope", LIB)) == set()


async def test_no_scope_returns_none() -> None:
    store = await _store_with_mirror()
    orch = _orch(store)
    assert await orch.resolve_scope(None) is None
    assert await orch.resolve_scope(RetrievalScope("", "", "")) is None


# ── 跨通道硬过滤 ─────────────────────────────────────────────────


class _UnfilteredVectors:
    """不支持过滤下推的稠密通道：无论作用域如何都返回全部候选，硬过滤必须由编排器兜底。"""

    def __init__(self, chunk_ids: list[str]) -> None:
        self._chunk_ids = chunk_ids

    async def search(self, collection: str, query_vector: list[float], top_k: int, **_: object):
        return [(cid, 1.0 - i * 0.1) for i, cid in enumerate(self._chunk_ids)][:top_k]


class _Embedding:
    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _dense_orch(store: InMemorySourceDocumentStore, tmp_path, chunk_ids: list[str]):
    compat = IndexCompatibilityStore(tmp_path / "compat.json")
    compat.mark_milvus_compatible("fp")
    orch = RetrievalOrchestrator(
        source_store=store,
        config=Config({"vector_db": {"backend": "milvus"}}),
        vector_store=_UnfilteredVectors(chunk_ids),  # type: ignore[arg-type]
        embedding_provider=_Embedding(),  # type: ignore[arg-type]
        index_compatibility=compat,
        embedding_fingerprint="fp",
    )
    chunks = {
        "dA_c0": DocumentChunk("dA_c0", "dA", 0, "alpha", "h"),
        "dB_c0": DocumentChunk("dB_c0", "dB", 0, "beta", "h"),
    }

    async def _hydrate(ids: list[str]) -> list[DocumentChunk]:
        # 内存仓储没有 SQL 连接；这里只替换「chunk_id → chunk」的取数，过滤逻辑仍走真实编排器。
        return [chunks[cid] for cid in ids if cid in chunks]

    orch._find_local_chunks = _hydrate  # type: ignore[method-assign]
    return orch


async def test_hard_filter_restricts_candidates_before_rrf(tmp_path) -> None:
    store = await _store_with_mirror()
    # 稠密通道返回 dA 与 dB 的 chunk；item 作用域应只保留 dA。
    orch = _dense_orch(store, tmp_path, ["dA_c0", "dB_c0"])
    outcome = await orch.retrieve_with_outcome(
        "kb", "alpha beta", top_k=5, scope=RetrievalScope(SCOPE_ITEM, "ITEMA", LIB)
    )
    assert [c.doc_id for c in outcome.chunks] == ["dA"]


async def test_document_scope_filters_dense_candidates(tmp_path) -> None:
    store = await _store_with_mirror()
    orch = _dense_orch(store, tmp_path, ["dA_c0", "dB_c0"])
    outcome = await orch.retrieve_with_outcome(
        "kb", "alpha beta", top_k=5, scope=RetrievalScope(SCOPE_DOCUMENT, "dB", LIB)
    )
    assert [c.doc_id for c in outcome.chunks] == ["dB"]


async def test_empty_scope_returns_no_chunks(tmp_path) -> None:
    store = await _store_with_mirror()
    orch = _dense_orch(store, tmp_path, ["dA_c0"])
    outcome = await orch.retrieve_with_outcome(
        "kb", "alpha", top_k=5, scope=RetrievalScope(SCOPE_ITEM, "NONEXIST", LIB)
    )
    assert outcome.chunks == []


# ── 图谱越界拒绝 ─────────────────────────────────────────────────


async def test_lightrag_context_rejects_item_and_tag_scope() -> None:
    store = await _store_with_mirror()
    orch = _orch(store)
    with pytest.raises(RuntimeError, match="item/tag/document scope"):
        await orch.retrieve_lightrag_context("kb", "q", RetrievalScope(SCOPE_ITEM, "ITEMA", LIB))
    with pytest.raises(RuntimeError, match="item/tag/document scope"):
        await orch.retrieve_lightrag_context("kb", "q", RetrievalScope(SCOPE_TAG, "shared", LIB))
    with pytest.raises(RuntimeError, match="item/tag/document scope"):
        await orch.retrieve_lightrag_context("kb", "q", RetrievalScope(SCOPE_DOCUMENT, "dA", LIB))
