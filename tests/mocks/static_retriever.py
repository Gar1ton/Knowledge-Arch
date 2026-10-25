"""检索编排器的内存替身（无 I/O，供接口对换测试）。

按 collection 持有固定分块，检索用确定性的朴素子串命中计数排序——只为验证业务编排契约
（引用、作答、存库），不追求召回质量。引擎标为 sqlite_lexical，与无向量库时的真实路径一致。
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from kacore.pipelines.retrieval_orchestrator import RetrievalOutcome

if TYPE_CHECKING:
    from kacore.domain.models import DocumentChunk


class StaticRetriever:
    """满足 api 对 RetrievalOrchestrator 的最小用法：retrieve / retrieve_with_outcome。"""

    def __init__(self, data: dict[str, list[DocumentChunk]] | None = None) -> None:
        self._data: dict[str, list[DocumentChunk]] = data or {}
        self.queries: list[tuple[str, str, int]] = []

    def _search(self, collection: str, query: str, top_k: int) -> list[DocumentChunk]:
        self.queries.append((collection, query, top_k))
        if top_k <= 0:
            return []
        q = query.lower()
        hits = [c for c in self._data.get(collection, []) if c.text.lower().count(q) > 0]
        hits.sort(key=lambda c: (-c.text.lower().count(q), c.ordinal))
        return [copy.deepcopy(c) for c in hits[:top_k]]

    async def retrieve(
        self, collection: str, query: str, top_k: int = 5, scope: Any = None
    ) -> list[DocumentChunk]:
        return self._search(collection, query, top_k)

    async def retrieve_with_outcome(
        self, collection: str, query: str, top_k: int = 5, scope: Any = None, **_: Any
    ) -> RetrievalOutcome:
        chunks = self._search(collection, query, top_k)
        return RetrievalOutcome(chunks=chunks, engines=["sqlite_lexical"] if chunks else [])

    async def retrieve_lightrag_context(
        self, collection: str, query: str, scope: Any = None
    ) -> str:
        raise RuntimeError("StaticRetriever has no LightRAG context")


__all__ = ["StaticRetriever"]
