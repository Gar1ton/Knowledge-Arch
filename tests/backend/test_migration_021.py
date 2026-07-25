"""Migration 021: unified chat sessions and immutable message metadata."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from kacore.migration_runner import run_migrations
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "ka_migrations"


async def test_021_upgrades_legacy_history_and_is_idempotent() -> None:
    conn = await aiosqlite.connect(":memory:")
    pre_021 = sorted(
        path for path in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql") if path.name < "021"
    )
    for path in pre_021:
        await conn.executescript(path.read_text(encoding="utf-8"))
    await conn.execute(
        "INSERT INTO chat_history "
        "(conversation_id, role, content, sources, retrieval_mode, created_at) "
        "VALUES ('legacy', 'assistant', 'old answer', '[]', '', '2026-01-01')"
    )
    await conn.commit()

    # Register already-applied migrations, then let the production runner apply 021.
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT)"
    )
    for path in pre_021:
        await conn.execute(
            "INSERT OR IGNORE INTO _migrations (name, applied_at) VALUES (?, '2026-01-01')",
            (path.name,),
        )
    await conn.commit()
    await run_migrations(conn)
    await run_migrations(conn)

    async with conn.execute("PRAGMA table_info(chat_history)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    assert "metadata" in columns
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_sessions'"
    ) as cursor:
        assert await cursor.fetchone() is not None

    messages = await SQLiteSourceDocumentStore(conn).get_chat_messages("legacy")
    assert messages[0]["metadata"] == {}
    await conn.close()
