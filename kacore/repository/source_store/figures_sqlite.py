"""FigureRepository 的 SQLite 实现（混入 SQLiteSourceDocumentStore，共用其连接与写锁）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kacore.domain.figures import DocumentFigure

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

_COLUMNS = (
    "figure_id, doc_id, page, ordinal, bbox, kind, label, caption, inner_text, status, "
    "description, model, prompt_version, image_hash, error, meta, updated_at"
)


def _row_to_figure(row: Any) -> DocumentFigure:
    bbox = json.loads(row[4])
    return DocumentFigure(
        figure_id=row[0],
        doc_id=row[1],
        page=int(row[2]),
        ordinal=int(row[3]),
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        kind=row[5],
        label=row[6],
        caption=row[7],
        inner_text=row[8],
        status=row[9],
        description=row[10],
        model=row[11],
        prompt_version=row[12],
        image_hash=row[13],
        error=row[14],
        meta=json.loads(row[15] or "{}"),
        updated_at=row[16],
    )


def _figure_params(fig: DocumentFigure) -> tuple[Any, ...]:
    return (
        fig.figure_id, fig.doc_id, fig.page, fig.ordinal, json.dumps(list(fig.bbox)), fig.kind,
        fig.label, fig.caption, fig.inner_text, fig.status, fig.description, fig.model,
        fig.prompt_version, fig.image_hash, fig.error, json.dumps(fig.meta, ensure_ascii=False),
        fig.updated_at,
    )


class SQLiteFigureMixin:
    """写方法持 `_write_lock`（与源文档库其余写操作同一把锁，见 sqlite.py 的 `_locked_write`）。"""

    _db: aiosqlite.Connection
    _write_lock: asyncio.Lock

    async def replace_document_figures(self, doc_id: str, figures: list[DocumentFigure]) -> None:
        async with self._write_lock:
            try:
                await self._db.execute("DELETE FROM document_figures WHERE doc_id = ?", (doc_id,))
                if figures:
                    await self._db.executemany(
                        f"INSERT INTO document_figures ({_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [_figure_params(fig) for fig in figures],
                    )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def list_document_figures(self, doc_id: str) -> list[DocumentFigure]:
        async with self._db.execute(
            f"SELECT {_COLUMNS} FROM document_figures WHERE doc_id = ? ORDER BY page, ordinal",
            (doc_id,),
        ) as cursor:
            return [_row_to_figure(row) for row in await cursor.fetchall()]

    async def list_figures(
        self, *, statuses: set[str] | None = None, doc_ids: list[str] | None = None
    ) -> list[DocumentFigure]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses is not None:
            if not statuses:
                return []
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(sorted(statuses))
        if doc_ids is not None:
            if not doc_ids:
                return []
            clauses.append(f"doc_id IN ({','.join('?' for _ in doc_ids)})")
            params.extend(doc_ids)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        async with self._db.execute(
            f"SELECT {_COLUMNS} FROM document_figures {where}ORDER BY doc_id, page, ordinal",
            params,
        ) as cursor:
            return [_row_to_figure(row) for row in await cursor.fetchall()]

    async def update_figure_result(self, figure: DocumentFigure) -> bool:
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "UPDATE document_figures SET status = ?, description = ?, model = ?, "
                    "prompt_version = ?, image_hash = ?, error = ?, updated_at = ? "
                    "WHERE figure_id = ?",
                    (figure.status, figure.description, figure.model, figure.prompt_version,
                     figure.image_hash, figure.error, figure.updated_at, figure.figure_id),
                )
                await self._db.commit()
                return (cursor.rowcount or 0) > 0
            except BaseException:
                await self._db.rollback()
                raise

    async def count_figures_by_status(self, doc_ids: list[str] | None = None) -> dict[str, int]:
        if doc_ids is not None and not doc_ids:
            return {}
        where = ""
        params: list[Any] = []
        if doc_ids is not None:
            where = f"WHERE doc_id IN ({','.join('?' for _ in doc_ids)}) "
            params = list(doc_ids)
        async with self._db.execute(
            f"SELECT status, COUNT(*) FROM document_figures {where}GROUP BY status", params
        ) as cursor:
            return {row[0]: int(row[1]) for row in await cursor.fetchall()}


__all__ = ["SQLiteFigureMixin"]
