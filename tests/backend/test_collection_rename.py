"""集合改名级联：文档、索引状态、集合笔记、图谱映射跟到新名字；启动时修复旧版改名留下的断链。"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from kacore.api import KnowledgeRepositoryApi
from kacore.domain.models import Collection, ScopedNote, SourceDocument
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
    """两个同名「papers」：A 在根下，B 在 other 下；d1 属于 A，d2 属于 B。"""
    for col in (
        Collection(name="papers", coll_key="LA"),
        Collection(name="other", coll_key="LO"),
        Collection(name="papers", coll_key="LB", parent_key="LO"),
    ):
        await store.upsert_collection(col)
    await store.add_document(_doc("d1", "papers"))
    await store.add_document(_doc("d2", "papers"))
    await store.set_document_collections("d1", ["LA"])
    await store.set_document_collections("d2", ["LB"])
    await store.set_lightrag_index_status("d1", "papers", "indexed")
    now = datetime.now(timezone.utc)
    await store.add_scoped_note(ScopedNote(
        id="n1", scope_type="collection", scope_key="papers", content="note",
        collection_name="papers", created_at=now, updated_at=now,
    ))


async def test_rename_moves_only_this_collections_documents(store) -> None:
    await _seed(store)
    api = KnowledgeRepositoryApi(source_store=store)

    await api.rename_collection("LA", "survey")

    assert [d.doc_id for d in await store.list_documents(collection="survey")] == ["d1"]
    assert [d.doc_id for d in await store.list_documents(collection="papers")] == ["d2"]
    status = await store.get_lightrag_index_status("d1")
    assert status is not None and status["collection"] == "survey"
    # 同名的 B 仍叫 papers，集合笔记按名字归属，跟着第一个改名的走（名字级记录的已知局限）。
    assert [n.id for n in await store.list_scoped_notes("collection", "survey")] == ["n1"]


async def test_rename_is_refused_while_a_graph_build_runs(store) -> None:
    from kacore.lightrag_core import BuildJob

    await _seed(store)
    api = KnowledgeRepositoryApi(source_store=store)
    api._graph_build_jobs["j1"] = BuildJob(job_id="j1", collection="papers", status="running")

    with pytest.raises(ValueError, match="being built"):
        await api.rename_collection("LA", "survey")
    assert (await store.get_collection("LA")).name == "papers"


async def test_startup_repair_fixes_documents_left_by_an_old_rename(store) -> None:
    """旧版只改了集合名：文档仍记着已不存在的旧名字，启动时据唯一的本地归属修回来。"""
    await store.upsert_collection(Collection(name="renamed", coll_key="LA"))
    await store.add_document(_doc("d1", "old-name"))
    await store.set_document_collections("d1", ["LA"])
    api = KnowledgeRepositoryApi(source_store=store)

    assert await api.repair_renamed_collections() == 1
    assert [d.doc_id for d in await store.list_documents(collection="renamed")] == ["d1"]
    assert await api.repair_renamed_collections() == 0  # 幂等
