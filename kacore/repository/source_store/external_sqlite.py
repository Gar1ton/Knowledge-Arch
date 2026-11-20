"""外部来源账本与推送账本的 SQLite 实现（混入 SQLiteSourceDocumentStore，共用其连接与写锁）。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kacore.domain.external_ledger import (
    Capture,
    CaptureHit,
    Citation,
    FieldOrigin,
    PushEntry,
    StoredSource,
)

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

_SOURCE_COLS = "source_id, trust, aliases, meta, reference_ids, placement, first_seen, checked_at"
_PUSH_COLS = ("doc_id, status, library_id, item_key, target_coll, file_path, source_path, "
              "message, updated_at, pushed_at, adopted_at")


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_to_capture(row: Any) -> Capture:
    return Capture(capture_id=row[0], conv_id=row[1], msg_idx=int(row[2]), question=row[3],
                   queries=json.loads(row[4] or "[]"), engines=json.loads(row[5] or "[]"),
                   actor=row[6], captured_at=row[7])


def _row_to_source(row: Any) -> StoredSource:
    return StoredSource(source_id=row[0], trust=row[1], aliases=json.loads(row[2] or "[]"),
                        meta=json.loads(row[3] or "{}"), reference_ids=json.loads(row[4] or "[]"),
                        placement=json.loads(row[5] or "{}"), first_seen=row[6], checked_at=row[7])


def _row_to_push(row: Any) -> PushEntry:
    return PushEntry(doc_id=row[0], status=row[1], library_id=row[2], item_key=row[3],
                     target_coll=row[4], file_path=row[5], source_path=row[6], message=row[7],
                     updated_at=row[8], pushed_at=row[9], adopted_at=row[10])


class SQLiteExternalMixin:
    """写方法持 `_write_lock`（与源文档库其余写操作同一把锁，见 sqlite.py 的 `_locked_write`）。"""

    _db: aiosqlite.Connection
    _write_lock: asyncio.Lock

    # ── 外部来源账本 ────────────────────────────────────────

    async def save_capture(
        self,
        capture: Capture,
        sources: list[StoredSource],
        hits: list[CaptureHit],
        fields: list[FieldOrigin],
    ) -> None:
        async with self._write_lock:
            try:
                await self._db.execute(
                    "INSERT INTO web_captures (capture_id, conv_id, msg_idx, question, queries, "
                    "engines, actor, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (capture.capture_id, capture.conv_id, capture.msg_idx, capture.question,
                     _dumps(capture.queries), _dumps(capture.engines), capture.actor,
                     capture.captured_at),
                )
                for s in sources:
                    await self._db.execute(
                        f"INSERT INTO web_sources ({_SOURCE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source_id) DO UPDATE SET trust = excluded.trust, "
                        "aliases = excluded.aliases, meta = excluded.meta, "
                        "reference_ids = excluded.reference_ids, "
                        "checked_at = excluded.checked_at, "
                        "placement = CASE WHEN excluded.placement = '{}' "
                        "THEN web_sources.placement ELSE excluded.placement END",
                        (s.source_id, s.trust, _dumps(s.aliases), _dumps(s.meta),
                         _dumps(s.reference_ids), _dumps(s.placement), s.first_seen, s.checked_at),
                    )
                await self._db.executemany(
                    "INSERT OR REPLACE INTO web_capture_hits (capture_id, source_id, engine, "
                    "query, rank, is_new, request_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(h.capture_id, h.source_id, h.engine, h.query, h.rank, int(h.is_new),
                      h.request_url) for h in hits],
                )
                await self._db.executemany(
                    "INSERT OR REPLACE INTO web_source_fields (source_id, field, engine, value, "
                    "fetched_at) VALUES (?, ?, ?, ?, ?)",
                    [(f.source_id, f.field, f.engine, f.value, f.fetched_at) for f in fields],
                )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def save_citations(self, capture_id: str, citations: list[Citation]) -> None:
        async with self._write_lock:
            try:
                await self._db.execute("DELETE FROM web_citations WHERE capture_id = ?",
                                       (capture_id,))
                await self._db.executemany(
                    "INSERT INTO web_citations (capture_id, source_id, ord, passage, page, "
                    "file_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                    [(capture_id, c.source_id, c.ord, c.passage, c.page, c.file_sha256)
                     for c in citations],
                )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def update_placements(self, placements: dict[str, dict[str, object]]) -> None:
        async with self._write_lock:
            try:
                await self._db.executemany(
                    "UPDATE web_sources SET placement = ? WHERE source_id = ?",
                    [(_dumps(p), sid) for sid, p in placements.items()],
                )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def get_capture(self, capture_id: str) -> Capture | None:
        async with self._db.execute(
            "SELECT capture_id, conv_id, msg_idx, question, queries, engines, actor, captured_at "
            "FROM web_captures WHERE capture_id = ?", (capture_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_capture(row) if row else None

    async def list_captures(self, conv_id: str) -> list[Capture]:
        async with self._db.execute(
            "SELECT capture_id, conv_id, msg_idx, question, queries, engines, actor, captured_at "
            "FROM web_captures WHERE conv_id = ? ORDER BY captured_at, capture_id", (conv_id,),
        ) as cursor:
            return [_row_to_capture(r) for r in await cursor.fetchall()]

    async def list_capture_hits(self, capture_ids: list[str]) -> list[CaptureHit]:
        if not capture_ids:
            return []
        marks = ",".join("?" for _ in capture_ids)
        async with self._db.execute(
            "SELECT capture_id, source_id, engine, query, rank, is_new, request_url "
            f"FROM web_capture_hits WHERE capture_id IN ({marks})", tuple(capture_ids),
        ) as cursor:
            rows = await cursor.fetchall()
        order = {cid: i for i, cid in enumerate(capture_ids)}
        hits = [CaptureHit(r[0], r[1], r[2], r[3], int(r[4]), bool(r[5]), r[6]) for r in rows]
        return sorted(hits, key=lambda h: (order[h.capture_id], h.engine, h.rank))

    async def list_citations(self, capture_id: str) -> list[Citation]:
        async with self._db.execute(
            "SELECT capture_id, source_id, ord, passage, page, file_sha256 FROM web_citations "
            "WHERE capture_id = ? ORDER BY ord", (capture_id,),
        ) as cursor:
            return [Citation(r[0], r[1], int(r[2]), r[3], r[4], r[5])
                    for r in await cursor.fetchall()]

    async def get_sources(self, source_ids: list[str]) -> dict[str, StoredSource]:
        if not source_ids:
            return {}
        marks = ",".join("?" for _ in source_ids)
        async with self._db.execute(
            f"SELECT {_SOURCE_COLS} FROM web_sources WHERE source_id IN ({marks})",
            tuple(source_ids),
        ) as cursor:
            return {r[0]: _row_to_source(r) for r in await cursor.fetchall()}

    async def list_source_fields(self, source_id: str) -> list[FieldOrigin]:
        async with self._db.execute(
            "SELECT source_id, field, engine, value, fetched_at FROM web_source_fields "
            "WHERE source_id = ? ORDER BY field, engine", (source_id,),
        ) as cursor:
            return [FieldOrigin(*r) for r in await cursor.fetchall()]

    # ── Zotero 推送账本 ─────────────────────────────────────

    async def upsert_push(self, entry: PushEntry) -> None:
        async with self._write_lock:
            try:
                await self._db.execute(
                    f"INSERT OR REPLACE INTO zotero_push_ledger ({_PUSH_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry.doc_id, entry.status, entry.library_id, entry.item_key,
                     entry.target_coll, entry.file_path, entry.source_path, entry.message,
                     entry.updated_at, entry.pushed_at, entry.adopted_at),
                )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def get_push(self, doc_id: str) -> PushEntry | None:
        async with self._db.execute(
            f"SELECT {_PUSH_COLS} FROM zotero_push_ledger WHERE doc_id = ?", (doc_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_push(row) if row else None

    async def find_push_by_item(self, library_id: str, item_key: str) -> PushEntry | None:
        async with self._db.execute(
            f"SELECT {_PUSH_COLS} FROM zotero_push_ledger WHERE library_id = ? AND item_key = ?",
            (library_id, item_key),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_push(row) if row else None

    async def list_pushes(self, statuses: set[str] | None = None) -> list[PushEntry]:
        sql = f"SELECT {_PUSH_COLS} FROM zotero_push_ledger"
        params: tuple[str, ...] = ()
        if statuses is not None:
            if not statuses:
                return []
            sql += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            params = tuple(sorted(statuses))
        async with self._db.execute(sql + " ORDER BY updated_at, doc_id", params) as cursor:
            return [_row_to_push(r) for r in await cursor.fetchall()]


__all__ = ["SQLiteExternalMixin"]
