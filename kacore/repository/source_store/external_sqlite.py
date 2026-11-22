"""联网工具调用账本的 SQLite 实现（混入 SQLiteSourceDocumentStore，共用其连接与写锁）。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kacore.domain.tool_calls import Block, Capture, Citation, ToolCall

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

_CAPTURE_COLS = "capture_id, conv_id, msg_idx, question, actor, captured_at"
_CALL_COLS = "call_id, capture_id, ord, tool, arguments, blocks, is_error, started_at, elapsed_ms"
_CITATION_COLS = "capture_id, ord, call_id, block, locator, page, fields, passage, cited, placement"


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _capture(row: Any) -> Capture:
    return Capture(row[0], row[1], int(row[2]), row[3], row[4], row[5])


def _call(row: Any) -> ToolCall:
    blocks = [Block(str(b.get("text") or ""), str(b.get("locator") or ""), b.get("page"))
              for b in json.loads(row[5] or "[]")]
    return ToolCall(row[0], row[1], int(row[2]), row[3], json.loads(row[4] or "{}"), blocks,
                    bool(row[6]), row[7], int(row[8]))


def _citation(row: Any) -> Citation:
    return Citation(row[0], int(row[1]), row[2], int(row[3]), json.loads(row[6] or "{}"), row[7],
                    row[4], row[5], bool(row[8]), json.loads(row[9] or "{}"))


class SQLiteToolLedgerMixin:
    """写方法持 `_write_lock`（与源文档库其余写操作同一把锁，见 sqlite.py 的 `_locked_write`）。"""

    _db: aiosqlite.Connection
    _write_lock: asyncio.Lock

    async def _write(self, sql: str, params: tuple[Any, ...] | list[tuple[Any, ...]],
                     *, many: bool = False) -> None:
        async with self._write_lock:
            try:
                if many:
                    await self._db.executemany(sql, params)  # type: ignore[arg-type]
                else:
                    await self._db.execute(sql, params)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def save_capture(self, capture: Capture) -> None:
        await self._write(
            f"INSERT INTO web_captures ({_CAPTURE_COLS}) VALUES (?, ?, ?, ?, ?, ?)",
            (capture.capture_id, capture.conv_id, capture.msg_idx, capture.question,
             capture.actor, capture.captured_at),
        )

    async def save_tool_call(self, call: ToolCall) -> None:
        await self._write(
            f"INSERT INTO web_tool_calls ({_CALL_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call.call_id, call.capture_id, call.ord, call.tool, _dumps(call.arguments),
             _dumps([{"text": b.text, "locator": b.locator, "page": b.page}
                     for b in call.blocks]),
             int(call.is_error), call.started_at, call.elapsed_ms),
        )

    async def save_citation(self, citation: Citation) -> None:
        await self._write(
            f"INSERT INTO web_citations ({_CITATION_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (citation.capture_id, citation.ord, citation.call_id, citation.block,
             citation.locator, citation.page, _dumps(citation.fields), citation.passage,
             int(citation.cited), _dumps(citation.placement)),
        )

    async def finish_capture(self, capture_id: str, msg_idx: int, cited: set[int]) -> None:
        marks = ",".join("?" for _ in cited) or "NULL"
        async with self._write_lock:
            try:
                await self._db.execute("UPDATE web_captures SET msg_idx = ? WHERE capture_id = ?",
                                       (msg_idx, capture_id))
                await self._db.execute(
                    f"UPDATE web_citations SET cited = CASE WHEN ord IN ({marks}) "
                    "THEN 1 ELSE 0 END WHERE capture_id = ?", (*sorted(cited), capture_id),
                )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def update_placements(
        self, capture_id: str, placements: dict[int, dict[str, Any]]
    ) -> None:
        await self._write(
            "UPDATE web_citations SET placement = ? WHERE capture_id = ? AND ord = ?",
            [(_dumps(p), capture_id, ord_) for ord_, p in placements.items()], many=True,
        )

    async def get_capture(self, capture_id: str) -> Capture | None:
        async with self._db.execute(
            f"SELECT {_CAPTURE_COLS} FROM web_captures WHERE capture_id = ?", (capture_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return _capture(row) if row else None

    async def list_tool_calls(self, capture_id: str) -> list[ToolCall]:
        async with self._db.execute(
            f"SELECT {_CALL_COLS} FROM web_tool_calls WHERE capture_id = ? ORDER BY ord",
            (capture_id,),
        ) as cursor:
            return [_call(r) for r in await cursor.fetchall()]

    async def list_citations(self, capture_id: str) -> list[Citation]:
        async with self._db.execute(
            f"SELECT {_CITATION_COLS} FROM web_citations WHERE capture_id = ? ORDER BY ord",
            (capture_id,),
        ) as cursor:
            return [_citation(r) for r in await cursor.fetchall()]

    async def list_recent_tool_calls(self, limit: int) -> list[tuple[ToolCall, Capture]]:
        cols = ", ".join(f"t.{c.strip()}" for c in _CALL_COLS.split(","))
        caps = ", ".join(f"c.{c.strip()}" for c in _CAPTURE_COLS.split(","))
        async with self._db.execute(
            f"SELECT {cols}, {caps} FROM web_tool_calls t JOIN web_captures c "
            "ON c.capture_id = t.capture_id ORDER BY t.started_at DESC, t.ord DESC LIMIT ?",
            (max(1, int(limit)),),
        ) as cursor:
            rows = await cursor.fetchall()
        return [(_call(r[:9]), _capture(r[9:])) for r in rows]


__all__ = ["SQLiteToolLedgerMixin"]
