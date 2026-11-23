"""联网工具调用账本的内存实现（混入 InMemorySourceDocumentStore，供接口对换测试）。"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kacore.domain.tool_calls import Capture, Citation, ToolCall


class InMemoryToolLedgerMixin:
    """深拷贝进出，行为与 SQLite 实现一致。属性由 InMemorySourceDocumentStore 初始化。"""

    _web_captures: dict[str, Capture]
    _web_calls: dict[str, ToolCall]
    _web_citations: dict[tuple[str, int], Citation]

    async def save_capture(self, capture: Capture) -> None:
        if capture.capture_id in self._web_captures:
            raise ValueError(f"duplicate capture_id: {capture.capture_id}")
        self._web_captures[capture.capture_id] = copy.deepcopy(capture)

    async def save_tool_call(self, call: ToolCall) -> None:
        if call.call_id in self._web_calls or call.capture_id not in self._web_captures:
            raise ValueError(f"cannot record call {call.call_id}")
        self._web_calls[call.call_id] = copy.deepcopy(call)

    async def save_citation(self, citation: Citation) -> None:
        key = (citation.capture_id, citation.ord)
        if key in self._web_citations:
            raise ValueError(f"duplicate citation: {key}")
        self._web_citations[key] = copy.deepcopy(citation)

    async def finish_capture(self, capture_id: str, msg_idx: int, cited: set[int]) -> None:
        capture = self._web_captures.get(capture_id)
        if capture is None:
            return
        capture.msg_idx = msg_idx
        for (cid, ord_), citation in self._web_citations.items():
            if cid == capture_id:
                citation.cited = ord_ in cited

    async def update_placements(
        self, capture_id: str, placements: dict[int, dict[str, Any]]
    ) -> None:
        for ord_, placement in placements.items():
            citation = self._web_citations.get((capture_id, ord_))
            if citation is not None:
                citation.placement = copy.deepcopy(placement)

    async def get_capture(self, capture_id: str) -> Capture | None:
        return copy.deepcopy(self._web_captures.get(capture_id))

    async def list_tool_calls(self, capture_id: str) -> list[ToolCall]:
        return sorted((copy.deepcopy(c) for c in self._web_calls.values()
                       if c.capture_id == capture_id), key=lambda c: c.ord)

    async def list_citations(self, capture_id: str) -> list[Citation]:
        return sorted((copy.deepcopy(c) for (cid, _), c in self._web_citations.items()
                       if cid == capture_id), key=lambda c: c.ord)

    async def list_recent_tool_calls(self, limit: int) -> list[tuple[ToolCall, Capture]]:
        calls = sorted(self._web_calls.values(), key=lambda c: (c.started_at, c.ord), reverse=True)
        return [(copy.deepcopy(c), copy.deepcopy(self._web_captures[c.capture_id]))
                for c in calls[: max(1, int(limit))]]


__all__ = ["InMemoryToolLedgerMixin"]
