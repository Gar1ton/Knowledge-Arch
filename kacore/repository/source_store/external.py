"""联网工具调用账本的仓储契约（ABC，迁移 029_tool_calls）。

SourceDocumentStore 继承本接口；实现见 external_sqlite / external_memory（与图空位一样单列，
因为 source_store 的 base / sqlite / memory 都已过 600 行）。契约是唯一真相源。
每次写入各自提交：一次运行中途出错，已发生的调用仍留在账本里，便于排查。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kacore.domain.tool_calls import Capture, Citation, ToolCall


class ToolLedgerRepository(ABC):
    @abstractmethod
    async def save_capture(self, capture: Capture) -> None:
        """新建一次运行；capture_id 重复时抛异常（调用方负责生成唯一编号）。"""
        ...

    @abstractmethod
    async def save_tool_call(self, call: ToolCall) -> None:
        """记一次工具调用；call_id 重复或 capture 不存在时抛异常。"""
        ...

    @abstractmethod
    async def save_citation(self, citation: Citation) -> None:
        """记一条通过核验的引用；(capture_id, ord) 重复时抛异常。"""
        ...

    @abstractmethod
    async def finish_capture(self, capture_id: str, msg_idx: int, cited: set[int]) -> None:
        """回答定稿后：写上回答在对话里的序号，并标出正文用到的引用（cited 里的 ord 置用到，
        其余置未用）。capture 不存在时什么也不做。"""
        ...

    @abstractmethod
    async def update_placements(
        self, capture_id: str, placements: dict[int, dict[str, Any]]
    ) -> None:
        """写回按用户集合树的归类（ord → {path / new_direction / reason}）；未知 ord 忽略。"""
        ...

    @abstractmethod
    async def get_capture(self, capture_id: str) -> Capture | None:
        """取一次运行；不存在返回 None。"""
        ...

    @abstractmethod
    async def list_tool_calls(self, capture_id: str) -> list[ToolCall]:
        """某次运行的全部调用，按 ord 升序。"""
        ...

    @abstractmethod
    async def list_citations(self, capture_id: str) -> list[Citation]:
        """某次运行的引用，按 ord 升序。"""
        ...

    @abstractmethod
    async def list_recent_tool_calls(self, limit: int) -> list[tuple[ToolCall, Capture]]:
        """最近的调用（跨运行，按 started_at 降序），带所属运行；工具面板的「最近调用」用。"""
        ...


__all__ = ["ToolLedgerRepository"]
