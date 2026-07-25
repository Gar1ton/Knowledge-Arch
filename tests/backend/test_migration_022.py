"""Migration 022: allow archived Notion ledger tombstones."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from kacore.migration_runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "ka_migrations"


@pytest.mark.asyncio
async def test_022_preserves_existing_ledger_rows_and_allows_archived_status() -> None:
    """升级账本约束时不得丢失既有记录，并允许 Strict 归档 tombstone。"""
    conn = await aiosqlite.connect(":memory:")
    try:
        pre_022 = sorted(
            path for path in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql") if path.name < "022"
        )
        for path in pre_022:
            await conn.executescript(path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO notion_entity_map "
            "(entity_type, entity_key, page_id, status) VALUES (?, ?, ?, ?)",
            ("document", "d1", "page-1", "failed"),
        )
        await conn.commit()

        await conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
        for path in pre_022:
            await conn.execute(
                "INSERT OR IGNORE INTO _migrations (name, applied_at) VALUES (?, '2026-01-01')",
                (path.name,),
            )
        await conn.commit()

        await run_migrations(conn)
        await run_migrations(conn)

        await conn.execute(
            "INSERT INTO notion_entity_map (entity_type, entity_key, status) VALUES (?, ?, ?)",
            ("document", "d2", "archived"),
        )
        await conn.commit()

        async with conn.execute(
            "SELECT entity_key, page_id, status FROM notion_entity_map ORDER BY entity_key"
        ) as cursor:
            rows = await cursor.fetchall()
        assert rows == [("d1", "page-1", "failed"), ("d2", "", "archived")]
    finally:
        await conn.close()
