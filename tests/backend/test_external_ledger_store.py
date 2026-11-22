"""调用账本仓储的接口对换测试：同一组断言跑内存与 SQLite 两个实现。"""
from __future__ import annotations

import aiosqlite
import pytest

from kacore.domain.tool_calls import Block, Capture, Citation, ToolCall
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


def _call(cid: str, ord_: int, at: str) -> ToolCall:
    return ToolCall(f"{cid}.{ord_}", cid, ord_, "search", {"query": "q"},
                    [Block("Title: A", "page 3", 3)], False, at, 12)


async def test_calls_and_citations_round_trip_with_blocks_and_pages(store) -> None:
    await store.save_capture(Capture("c1", "conv", 0, "question", "ui", "t0"))
    await store.save_tool_call(_call("c1", 1, "t1"))
    await store.save_citation(Citation("c1", 1, "c1.1", 0, {"title": "A"}, "passage",
                                       "page 3", 3))
    [call] = await store.list_tool_calls("c1")
    assert call.blocks == [Block("Title: A", "page 3", 3)] and call.arguments == {"query": "q"}
    [citation] = await store.list_citations("c1")
    assert (citation.page, citation.locator, citation.cited) == (3, "page 3", False)


async def test_finish_capture_sets_message_index_and_cited_flags(store) -> None:
    await store.save_capture(Capture("c1", "conv", 0, "question", "ui", "t0"))
    await store.save_tool_call(_call("c1", 1, "t1"))
    for ord_ in (1, 2):
        await store.save_citation(Citation("c1", ord_, "c1.1", 0, {}, f"p{ord_}"))
    await store.finish_capture("c1", 5, {2})
    assert (await store.get_capture("c1")).msg_idx == 5
    assert [c.cited for c in await store.list_citations("c1")] == [False, True]


async def test_duplicate_capture_id_is_rejected(store) -> None:
    await store.save_capture(Capture("c1", "conv", 0, "q", "ui", "t0"))
    with pytest.raises(Exception):  # noqa: B017 - 两个实现的异常类型不同
        await store.save_capture(Capture("c1", "conv", 0, "q", "ui", "t0"))


async def test_recent_calls_are_newest_first_across_captures(store) -> None:
    for cid, at in (("c1", "t1"), ("c2", "t2")):
        await store.save_capture(Capture(cid, "conv", 0, "q", "agent", at))
        await store.save_tool_call(_call(cid, 1, at))
    recent = await store.list_recent_tool_calls(10)
    assert [(c.capture_id, cap.actor) for c, cap in recent] == [("c2", "agent"), ("c1", "agent")]


async def test_migration_drops_the_unreleased_ledger_tables() -> None:
    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("CREATE TABLE web_sources (source_id TEXT)")
        await conn.execute("CREATE TABLE zotero_push_ledger (doc_id TEXT)")
        await run_migrations(conn)
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
            tables = {r[0] for r in await cur.fetchall()}
        assert {"web_captures", "web_tool_calls", "web_citations"} <= tables
        assert not tables & {"web_sources", "zotero_push_ledger"}
    finally:
        await conn.close()
