"""默认集合：不可删除 / 改名；删除其他集合后无归属的文档进默认集合；
启动时把旧版 `_uncategorized` 并回来。
"""
from __future__ import annotations

import aiosqlite
import pytest

from kacore.api import SYSTEM_COLLECTION_UNCATEGORIZED, KnowledgeRepositoryApi
from kacore.domain.models import Collection, SourceDocument
from kacore.migration_runner import run_migrations
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore


@pytest.fixture(params=["memory", "sqlite"])
async def store(request):
    if request.param == "memory":
        yield InMemorySourceDocumentStore()
        return
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)
    yield SQLiteSourceDocumentStore(conn)
    await conn.close()


def _doc(doc_id: str, collection: str) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id, title=doc_id, file_path=f"/data/{doc_id}.pdf",
        content_type="application/pdf", size_bytes=1, content_hash=doc_id, collection=collection,
    )


async def _seed(store) -> None:
    """d1 只属于 papers；d2 同时属于 papers 与 survey（主归属 papers）。"""
    for col in (
        Collection(name="default", coll_key="LD"),
        Collection(name="papers", coll_key="LP"),
        Collection(name="survey", coll_key="LS"),
    ):
        await store.upsert_collection(col)
    await store.add_document(_doc("d1", "papers"))
    await store.add_document(_doc("d2", "papers"))
    await store.set_document_collections("d1", ["LP"])
    await store.set_document_collections("d2", ["LP", "LS"])


async def test_default_collection_cannot_be_deleted_or_renamed(store) -> None:
    await _seed(store)
    api = KnowledgeRepositoryApi(source_store=store)

    with pytest.raises(ValueError, match="default collection"):
        await api.delete_collection_by_key("LD")
    with pytest.raises(ValueError, match="default collection"):
        await api.delete_collection("default")
    with pytest.raises(ValueError, match="default collection"):
        await api.rename_collection("LD", "inbox")
    assert (await store.get_collection("LD")).name == "default"


async def test_deleting_a_collection_moves_orphans_into_default(store) -> None:
    await _seed(store)
    api = KnowledgeRepositoryApi(source_store=store)

    assert await api.delete_collection_by_key("LP") is True

    names = {c.name for c in await store.list_collections()}
    assert SYSTEM_COLLECTION_UNCATEGORIZED not in names
    d1 = await store.get_document("d1")
    assert d1.collection == "default"
    assert await store.list_document_collection_keys("d1") == ["LD"]
    # 还有别的归属：不进默认集合，主归属改到剩下的集合，不留已删除的名字。
    d2 = await store.get_document("d2")
    assert d2.collection == "survey"
    assert await store.list_document_collection_keys("d2") == ["LS"]


async def test_delete_by_name_also_moves_into_default(store) -> None:
    await _seed(store)
    api = KnowledgeRepositoryApi(source_store=store)

    assert await api.delete_collection("papers") is True

    assert {d.doc_id for d in await store.list_documents(collection="default")} == {"d1", "d2"}
    assert SYSTEM_COLLECTION_UNCATEGORIZED not in {c.name for c in await store.list_collections()}


async def test_startup_merges_legacy_uncategorized_into_default(store) -> None:
    """旧版删除 default 后：文档在 `_uncategorized`，default 被启动重建成空集合。"""
    await store.upsert_collection(Collection(name="default", coll_key="LD"))
    await store.upsert_collection(Collection(name=SYSTEM_COLLECTION_UNCATEGORIZED, coll_key="LU"))
    await store.add_document(_doc("d1", SYSTEM_COLLECTION_UNCATEGORIZED))
    await store.set_document_collections("d1", ["LU"])
    api = KnowledgeRepositoryApi(source_store=store)

    assert await api.merge_legacy_uncategorized() == 1

    assert [d.doc_id for d in await store.list_documents(collection="default")] == ["d1"]
    assert await store.list_document_collection_keys("d1") == ["LD"]
    assert await store.get_collection("LU") is None
    assert await api.merge_legacy_uncategorized() == 0  # 幂等


async def test_merge_recreates_default_when_it_is_missing(store) -> None:
    await store.upsert_collection(Collection(name=SYSTEM_COLLECTION_UNCATEGORIZED, coll_key="LU"))
    await store.add_document(_doc("d1", SYSTEM_COLLECTION_UNCATEGORIZED))
    await store.set_document_collections("d1", ["LU"])
    api = KnowledgeRepositoryApi(source_store=store)

    assert await api.merge_legacy_uncategorized() == 1
    default = await store.get_collection_by_name("default")
    assert default is not None
    assert await store.list_document_collection_keys("d1") == [default.coll_key]
