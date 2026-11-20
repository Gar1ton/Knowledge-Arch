"""migration 029（外部来源账本）与 030（Zotero 推送账本）的建表与约束契约测试。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def _db_with_all_migrations() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        await conn.executescript(path.read_text(encoding="utf-8"))
        await conn.commit()
    return conn


async def test_029_records_capture_hits_and_citations() -> None:
    conn = await _db_with_all_migrations()
    await conn.execute(
        "INSERT INTO web_captures (capture_id, conv_id, msg_idx, question, actor, captured_at) "
        "VALUES ('c1', 'conv', 3, 'q', 'ui', '2026-11-20T10:15:00Z')")
    await conn.execute(
        "INSERT INTO web_sources (source_id, trust, first_seen, checked_at) "
        "VALUES ('doi:10.1234/a', 'verified', 't', 't')")
    await conn.execute(
        "INSERT INTO web_capture_hits (capture_id, source_id, engine, query, rank) "
        "VALUES ('c1', 'doi:10.1234/a', 'crossref', 'q', 1)")
    await conn.execute(
        "INSERT INTO web_citations (capture_id, source_id, ord, passage, page) "
        "VALUES ('c1', 'doi:10.1234/a', 1, 'text', 4)")
    await conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        await conn.execute(
            "INSERT INTO web_sources (source_id, trust, first_seen, checked_at) "
            "VALUES ('x', 'maybe', 't', 't')")
    # 删除一次 capture 级联删除它的命中与引用，文献本身保留
    await conn.execute("DELETE FROM web_captures WHERE capture_id = 'c1'")
    rows = await (await conn.execute("SELECT COUNT(*) FROM web_capture_hits")).fetchone()
    assert rows == (0,)
    rows = await (await conn.execute("SELECT COUNT(*) FROM web_sources")).fetchone()
    assert rows == (1,)
    await conn.close()


async def test_030_ledger_status_is_constrained() -> None:
    conn = await _db_with_all_migrations()
    await conn.execute(
        "INSERT INTO documents (doc_id, title, file_path, content_type, size_bytes, content_hash, "
        "created_at, updated_at) VALUES ('d1', 't', '/f.pdf', 'application/pdf', 1, 'h', 't', 't')")
    await conn.execute(
        "INSERT INTO zotero_push_ledger (doc_id, status, updated_at) VALUES ('d1', 'pushed', 't')")
    with pytest.raises(sqlite3.IntegrityError):
        await conn.execute(
            "INSERT INTO zotero_push_ledger (doc_id, status, updated_at) VALUES ('d1', 'x', 't')")
    await conn.close()
