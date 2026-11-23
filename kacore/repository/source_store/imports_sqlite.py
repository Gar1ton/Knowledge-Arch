"""外部论文入库账本的 SQLite 实现（混入 SQLiteSourceDocumentStore，共用其连接与写锁）。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kacore.domain.paper_import import ImportRecord

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

_IMPORT_COLS = ("import_id, capture_id, ord, fields, filename, target, status, doc_id, pdf_url, "
                "pdf_sha256, source, tried, created_at, updated_at")


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

    async def save_import(self, record: ImportRecord) -> None:
        params = (
            record.import_id, record.capture_id, record.ord,
            json.dumps(record.fields, ensure_ascii=False), record.filename, record.target,
            record.status, record.doc_id, record.pdf_url, record.pdf_sha256,
            json.dumps(record.source, ensure_ascii=False),
            json.dumps(record.tried, ensure_ascii=False), record.created_at, record.updated_at,
        )
        async with self._write_lock:
            try:
                await self._db.execute(
                    f"INSERT OR REPLACE INTO paper_imports ({_IMPORT_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", params)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

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


__all__ = ["SQLiteImportLedgerMixin"]
