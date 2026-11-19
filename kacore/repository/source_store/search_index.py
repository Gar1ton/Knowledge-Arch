"""分块关键词索引（FTS5）的写入与回填；表结构与删除同步见 `migrations/028_chunks_fts.sql`。

为何单独成文件：分块有两个写入点（`sqlite.py` 的 replace_chunks、`processing.py` 的
commit_processing），启动时还要补齐已有分块；三处共用这里的写入，保证派生文本只有一种算法。

契约：
- `index_chunks` 只做 INSERT，不 commit；调用方在写 chunks 的同一事务里调用，失败一起回滚。
  调用前该文档的旧分块已被 DELETE（触发器已删掉旧索引行），所以这里不处理重复。
- `backfill_search_index` 自己分批 commit，只在启动时（连接上没有其它写事务时）调用。
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from kacore.utils.search_text import search_text

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["index_chunks", "backfill_search_index"]

# 启动回填每批分块数：派生文本在 Python 里算，分批限住内存并让出事件循环。
_BACKFILL_BATCH = 1000


async def index_chunks(db: aiosqlite.Connection, chunks: Iterable[tuple[str, str]]) -> None:
    """为 (chunk_id, text) 写入关键词索引：先登记行号，再按行号写派生文本（两次批量语句）。"""
    rows = list(chunks)
    if not rows:
        return
    await db.executemany(
        "INSERT INTO chunk_search_rows (chunk_id) VALUES (?)", [(cid,) for cid, _ in rows]
    )
    await db.executemany(
        "INSERT INTO chunks_fts (rowid, body) "
        "SELECT row, ? FROM chunk_search_rows WHERE chunk_id = ?",
        [(search_text(text), cid) for cid, text in rows],
    )


async def backfill_search_index(db: aiosqlite.Connection) -> int:
    """给还没有关键词索引的分块补索引，返回补了多少条。已全部索引时只是一次空查询。"""
    total = 0
    while True:
        async with db.execute(
            "SELECT c.chunk_id, c.text FROM chunks c "
            "LEFT JOIN chunk_search_rows s ON s.chunk_id = c.chunk_id "
            "WHERE s.chunk_id IS NULL LIMIT ?",
            (_BACKFILL_BATCH,),
        ) as cursor:
            rows = [(row[0], row[1]) for row in await cursor.fetchall()]
        if not rows:
            return total
        try:
            await index_chunks(db, rows)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        total += len(rows)
