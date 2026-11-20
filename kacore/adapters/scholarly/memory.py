"""学术索引的内存实现（测试用，无网络）：按预置的检索结果与编号表回答。"""
from __future__ import annotations

from kacore.adapters.scholarly.base import ScholarlyIndex, ScholarlyIndexError
from kacore.domain.web_sources import IndexHit, Pid


class MemoryIndex(ScholarlyIndex):
    """results：检索词 → 命中列表（rank 按列表顺序重写）；records：Pid.key → 命中。

    fail=True 时每次调用抛 ScholarlyIndexError，用于测试某个索引失败时的降级。
    calls 记录调用（("search", query) / ("lookup", pid_key)），便于断言请求次数。
    """

    def __init__(
        self,
        name: str,
        *,
        results: dict[str, list[IndexHit]] | None = None,
        records: dict[str, IndexHit] | None = None,
        fail: bool = False,
    ) -> None:
        self.name = name
        self._results = results or {}
        self._records = records or {}
        self._fail = fail
        self.calls: list[tuple[str, str]] = []

    def search(self, query: str, limit: int) -> list[IndexHit]:
        self.calls.append(("search", query))
        if self._fail:
            raise ScholarlyIndexError(self.name, "unavailable")
        hits = self._results.get(query, [])[:limit]
        for rank, hit in enumerate(hits, start=1):
            hit.engine, hit.query, hit.rank = self.name, query, rank
        return hits

    def lookup(self, pid: Pid) -> IndexHit | None:
        self.calls.append(("lookup", pid.key))
        if self._fail:
            raise ScholarlyIndexError(self.name, "unavailable")
        return self._records.get(pid.key)


__all__ = ["MemoryIndex"]
