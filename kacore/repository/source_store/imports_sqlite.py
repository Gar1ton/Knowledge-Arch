"""外部论文入库与 Zotero 推送账本的 SQLite 实现。

混入 SQLiteSourceDocumentStore，共用其连接与写锁。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kacore.domain.paper_import import PUSH_CLAIMED, PUSH_PUSHED, ImportRecord, PushRecord

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

_IMPORT_COLS = ("import_id, capture_id, ord, fields, filename, target, status, doc_id, pdf_url, "
                "pdf_sha256, source, tried, created_at, updated_at")


_PUSH_COLS = ("doc_id, library_id, collection_key, attachment_key, path, file_path, status, "
              "message, write_token, pushed_at, claimed_at")


def _push(row: Any) -> PushRecord:
    return PushRecord(
        doc_id=row[0], library_id=row[1], collection_key=row[2], attachment_key=row[3],
        path=row[4], file_path=row[5], status=row[6], message=row[7], write_token=row[8],
        pushed_at=row[9], claimed_at=row[10],
    )


def _import(row: Any) -> ImportRecord:
    return ImportRecord(
        import_id=row[0], capture_id=row[1], ord=int(row[2]), fields=json.loads(row[3] or "{}"),
        filename=row[4], target=row[5], status=row[6], doc_id=row[7], pdf_url=row[8],
        pdf_sha256=row[9], source=json.loads(row[10] or "{}"), tried=json.loads(row[11] or "[]"),
        created_at=row[12], updated_at=row[13],
    )


class SQLiteImportLedgerMixin:
    """写方法持 `_write_lock`（与源文档库其余写操作同一把锁）。"""

    _db: aiosqlite.Connection
    _write_lock: asyncio.Lock

    async def _commit(self, sql: str, params: tuple[Any, ...]) -> None:
        async with self._write_lock:
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def save_import(self, record: ImportRecord) -> None:
        params = (
            record.import_id, record.capture_id, record.ord,
            json.dumps(record.fields, ensure_ascii=False), record.filename, record.target,
            record.status, record.doc_id, record.pdf_url, record.pdf_sha256,
            json.dumps(record.source, ensure_ascii=False),
            json.dumps(record.tried, ensure_ascii=False), record.created_at, record.updated_at,
        )
        await self._commit(f"INSERT OR REPLACE INTO paper_imports ({_IMPORT_COLS}) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", params)

    async def get_import(self, import_id: str) -> ImportRecord | None:
        async with self._db.execute(
            f"SELECT {_IMPORT_COLS} FROM paper_imports WHERE import_id = ?", (import_id,)
        ) as cur:
            row = await cur.fetchone()
        return _import(row) if row else None

    async def list_imports(
        self, *, status: str | None = None, capture_id: str | None = None,
        doc_id: str | None = None,
    ) -> list[ImportRecord]:
        where, params = [], []
        for column, value in (("status", status), ("capture_id", capture_id), ("doc_id", doc_id)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        sql = f"SELECT {_IMPORT_COLS} FROM paper_imports"
        if where:
            sql += " WHERE " + " AND ".join(where)
        async with self._db.execute(sql + " ORDER BY updated_at DESC", params) as cur:
            rows = await cur.fetchall()
        return [_import(r) for r in rows]

    async def save_push(self, record: PushRecord) -> None:
        await self._commit(
            f"INSERT OR REPLACE INTO zotero_pushes ({_PUSH_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.doc_id, record.library_id, record.collection_key, record.attachment_key,
             record.path, record.file_path, record.status, record.message, record.write_token,
             record.pushed_at, record.claimed_at))

    async def get_push(self, doc_id: str) -> PushRecord | None:
        async with self._db.execute(
            f"SELECT {_PUSH_COLS} FROM zotero_pushes WHERE doc_id = ?", (doc_id,)
        ) as cur:
            row = await cur.fetchone()
        return _push(row) if row else None

    async def list_pushes(self, status: str | None = None) -> list[PushRecord]:
        sql = f"SELECT {_PUSH_COLS} FROM zotero_pushes"
        params: tuple[Any, ...] = ()
        if status is not None:
            sql, params = sql + " WHERE status = ?", (status,)
        async with self._db.execute(sql + " ORDER BY pushed_at DESC", params) as cur:
            rows = await cur.fetchall()
        return [_push(r) for r in rows]

    async def push_claims(self) -> dict[str, str]:
        async with self._db.execute(
            "SELECT attachment_key, doc_id FROM zotero_pushes "
            "WHERE status IN (?, ?) AND attachment_key != ''", (PUSH_PUSHED, PUSH_CLAIMED)
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}


__all__ = ["SQLiteImportLedgerMixin"]
