"""外部来源账本 / 推送账本仓储的接口对换测试：同一组断言跑内存与 SQLite 两个实现。"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from kacore.domain.external_ledger import (
    PUSH_ADOPTED,
    PUSH_PUSHED,
    Capture,
    CaptureHit,
    Citation,
    FieldOrigin,
    PushEntry,
    StoredSource,
)
from kacore.domain.models import SourceDocument
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


def _capture(cid: str, at: str, msg_idx: int = 1) -> Capture:
    return Capture(cid, "conv-1", msg_idx, "question", ["q1"], ["crossref", "arxiv"], "ui", at)


def _source(sid: str, trust: str = "verified", placement: dict | None = None) -> StoredSource:
    return StoredSource(sid, trust, aliases=[sid], meta={"title": sid}, reference_ids=["doi:r"],
                        placement=placement or {}, first_seen="t1", checked_at="t1")


async def test_capture_round_trip_and_source_upsert_keeps_first_seen(store) -> None:
    await store.save_capture(
        _capture("c1", "2026-11-20T10:00"), [_source("doi:a", "single")],
        [CaptureHit("c1", "doi:a", "crossref", "q1", 2, True, "https://api/x")],
        [FieldOrigin("doi:a", "title", "crossref", "A", "t1")],
    )
    await store.update_placements({"doi:a": {"path": ["[26.3] Agentic"], "reason": "r"},
                                   "doi:unknown": {"path": []}})
    later = _source("doi:a", "verified")
    later.first_seen, later.checked_at = "t9", "t9"
    await store.save_capture(_capture("c2", "2026-11-20T11:00", 3), [later],
                             [CaptureHit("c2", "doi:a", "openalex", "q2", 5, False)], [])

    sources = await store.get_sources(["doi:a", "doi:missing"])
    assert list(sources) == ["doi:a"]
    assert sources["doi:a"].trust == "verified"
    assert sources["doi:a"].first_seen == "t1"                      # 首次写入时间不变
    assert sources["doi:a"].placement == {"path": ["[26.3] Agentic"], "reason": "r"}  # 空归类不覆盖

    captures = await store.list_captures("conv-1")
    assert [c.capture_id for c in captures] == ["c1", "c2"]
    assert captures[0].engines == ["crossref", "arxiv"]
    assert await store.get_capture("nope") is None

    hits = await store.list_capture_hits(["c2", "c1"])
    assert [(h.capture_id, h.rank, h.is_new) for h in hits] == [("c2", 5, False), ("c1", 2, True)]
    assert [f.engine for f in await store.list_source_fields("doi:a")] == ["crossref"]


async def test_duplicate_capture_id_is_rejected(store) -> None:
    await store.save_capture(_capture("c1", "t"), [], [], [])
    with pytest.raises(Exception):
        await store.save_capture(_capture("c1", "t"), [], [], [])


async def test_citations_are_replaced_as_a_batch(store) -> None:
    await store.save_capture(_capture("c1", "t"), [_source("doi:a")], [], [])
    await store.save_citations("c1", [Citation("c1", "doi:a", 1, "p4 text", 4, "sha"),
                                      Citation("c1", "doi:a", 2, "abstract", None)])
    await store.save_citations("c1", [Citation("c1", "doi:a", 1, "only", 7)])
    [only] = await store.list_citations("c1")
    assert (only.passage, only.page) == ("only", 7)


async def test_push_ledger_round_trip_and_lookup_by_item(store) -> None:
    now = datetime.now(timezone.utc)
    await store.add_document(SourceDocument(
        doc_id="d1", title="d1", file_path="/d1.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h", collection="default", created_at=now, updated_at=now,
    ))
    await store.upsert_push(PushEntry("d1", PUSH_PUSHED, "12595538", "ABCD2345", "COLL1",
                                      "/z/a.pdf", "/ka/a.pdf", "", "t1", "t1"))
    found = await store.find_push_by_item("12595538", "ABCD2345")
    assert found is not None and found.doc_id == "d1"
    found.status, found.adopted_at, found.updated_at = PUSH_ADOPTED, "t2", "t2"
    await store.upsert_push(found)
    assert [p.status for p in await store.list_pushes({PUSH_ADOPTED})] == [PUSH_ADOPTED]
    assert await store.list_pushes(set()) == []
    await store.delete_document("d1")
    assert await store.get_push("d1") is None                       # 随文档删除
