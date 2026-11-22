"""联网补充的调度：distributor 按任务内容挑工具调用，每次调用与每条引用都记账（managers 层）。

用户 11-22：「将每个联网功能适配成类似 MCP 的方式，允许一个 distributor 通过任务内容调用」。
- `ToolSession`：一次运行（一个回答的联网补充，或 AI 助手 / 工具面板的一组调用）。它经 `ToolBox`
  执行工具、把每次调用写进账本（含 cite 本身），并用 `trace_check` 核对 cite 登记的引用。
  助手经 skill 的调用跨多个 HTTP 请求，所以会话可以按 capture_id 从账本恢复。
- `ToolDistributor`：对话栏打开「联网补充」时，用 research 角色的模型做原生工具调用，循环最多
  `max_rounds` 轮（每轮一次模型调用）；最后一轮只给 cite，让模型把已读到的来源登记完。
  它只收集证据，不写回答。

可信与否只看 cite 能否逐字对上调用原文，模型不参与；没有任何工具名出现在这里。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from kacore.domain.tool_calls import (
    CITE_SPEC,
    CITE_TOOL,
    Block,
    Capture,
    Citation,
    CiteRequest,
    ResultTurn,
    ToolCall,
    ToolResult,
    Turn,
    UserTurn,
    normalize_text,
    render_blocks,
    trace_check,
)
from kacore.pipelines.deep_thinking_prompts import JsonContractError
from kacore.pipelines.external_prompts import (
    DISTRIBUTOR_SYSTEM,
    PLACE_SYSTEM,
    Candidate,
    Placement,
    parse_placements,
    render_call_result,
    render_cite_result,
    render_placement_prompt,
    render_task,
)
from kacore.pipelines.llm_json import llm_json_call

if TYPE_CHECKING:
    from kacore.adapters.tools.registry import ToolBox
    from kacore.config import ToolsConfig
    from kacore.repository.source_store.external import ToolLedgerRepository

logger = logging.getLogger("ToolDistributor")


class ExternalUnavailable(RuntimeError):
    """联网补充没法运行：没有可用工具、研究模型未分配，或研究模型不支持工具调用。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


REASON_NO_TOOLS = "no_tools"
REASON_NO_MODEL = "no_model"
REASON_UNSUPPORTED = "unsupported"


def call_label(ord_: int) -> str:
    """交给模型的调用编号：c1、c2…（本次运行内）。"""
    return f"c{ord_}"


class ToolSession:
    def __init__(self, toolbox: ToolBox, store: ToolLedgerRepository, capture: Capture) -> None:
        self._toolbox = toolbox
        self._store = store
        self.capture = capture
        self.calls: dict[str, ToolCall] = {}        # 键是 c1、c2…
        self.citations: list[Citation] = []
        self.rejected = 0

    @classmethod
    async def open(cls, toolbox: ToolBox, store: ToolLedgerRepository, *, conv_id: str,
                   question: str, actor: str) -> ToolSession:
        capture = Capture(f"c_{uuid.uuid4().hex[:12]}", conv_id, 0, question, actor, _now())
        await store.save_capture(capture)
        return cls(toolbox, store, capture)

    @classmethod
    async def resume(cls, toolbox: ToolBox, store: ToolLedgerRepository,
                     capture_id: str) -> ToolSession | None:
        capture = await store.get_capture(capture_id)
        if capture is None:
            return None
        session = cls(toolbox, store, capture)
        for call in await store.list_tool_calls(capture_id):
            session.calls[call_label(call.ord)] = call
        session.citations = await store.list_citations(capture_id)
        return session

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> tuple[ToolCall, str]:
        """执行一次调用（cite 或真正的工具）并记账；返回调用记录与交给模型的文本。"""
        if name == CITE_TOOL:
            return await self.cite(arguments)
        return await self.call(name, arguments)

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[ToolCall, str]:
        started, t0 = _now(), time.monotonic()
        result = await self._toolbox.call(name, arguments)
        record = await self._record(name, arguments, result, started, t0)
        label = call_label(record.ord)
        return record, render_call_result(label, name, render_blocks(record.blocks))

    async def cite(self, arguments: dict[str, Any]) -> tuple[ToolCall, str]:
        started, t0 = _now(), time.monotonic()
        request = CiteRequest.from_arguments(arguments)
        check = trace_check(request, self.calls)
        duplicate = self._duplicate(request, check)
        ord_: int | None = None
        if check.ok and duplicate is None:
            ord_ = len(self.citations) + 1
            source = self.calls[request.call]
            block = source.blocks[check.block]
            citation = Citation(self.capture.capture_id, ord_, source.call_id, check.block,
                                request.fields, request.passage, block.locator, block.page)
            self.citations.append(citation)     # 先占编号再写库：同一轮的 cite 并发执行
            await self._store.save_citation(citation)
        elif not check.ok:
            self.rejected += 1
        text = render_cite_result(check, ord_=ord_, label=request.call, duplicate_of=duplicate)
        result = ToolResult([Block(text)], is_error=not check.ok)
        record = await self._record(CITE_TOOL, arguments, result, started, t0)
        return record, text

    def citation(self, ord_: int) -> Citation | None:
        return next((c for c in self.citations if c.ord == ord_), None)

    def _duplicate(self, request: CiteRequest, check: Any) -> int | None:
        if not check.ok:
            return None
        source = self.calls[request.call]
        title = normalize_text(str(request.fields.get("title") or ""))
        for c in self.citations:
            same_place = c.call_id == source.call_id and c.block == check.block
            if same_place and normalize_text(str(c.fields.get("title") or "")) == title:
                return c.ord
        return None

    async def _record(self, name: str, arguments: dict[str, Any], result: ToolResult,
                      started: str, t0: float) -> ToolCall:
        ord_ = len(self.calls) + 1
        record = ToolCall(f"{self.capture.capture_id}.{ord_}", self.capture.capture_id, ord_,
                          name, dict(arguments), list(result.blocks), result.is_error, started,
                          int((time.monotonic() - t0) * 1000))
        self.calls[call_label(ord_)] = record
        await self._store.save_tool_call(record)
        return record


class ToolDistributor:
    """契约：`run` 在没有可用工具、未分配研究模型或模型不支持工具调用时抛 ExternalUnavailable
    （此时还没有建账本记录）；模型调用中途失败抛 ProviderError，已发生的调用留在账本里。"""

    def __init__(self, *, toolbox: ToolBox, store: ToolLedgerRepository, client: Any | None,
                 config_provider: Callable[[], ToolsConfig], llm: Any | None = None) -> None:
        self._toolbox = toolbox
        self._store = store
        self._client = client
        self._config = config_provider
        self._llm = llm

    @property
    def toolbox(self) -> ToolBox:
        return self._toolbox

    async def run(
        self,
        question: str,
        local_excerpts: Sequence[tuple[str, str]],
        *,
        conv_id: str,
        progress: Callable[[str], None] | None = None,
    ) -> ToolSession:
        from kacore.providers.base import ERROR_NO_TOOLS, ProviderError

        specs = self._toolbox.specs()
        if not specs:
            raise ExternalUnavailable(REASON_NO_TOOLS, "no external tool is enabled")
        if self._client is None or not getattr(self._client, "configured", True):
            raise ExternalUnavailable(REASON_NO_MODEL, "no research model is assigned")
        step = progress or (lambda _stage: None)
        rounds = self._config().max_rounds
        turns: list[Turn] = [UserTurn(render_task(question, list(local_excerpts)[:8]))]
        session: ToolSession | None = None
        for round_no in range(1, rounds + 1):
            offered = [CITE_SPEC] if round_no == rounds and rounds > 1 else [*specs, CITE_SPEC]
            step("external_round")
            try:
                reply = await self._client.generate_with_tools(turns, offered, DISTRIBUTOR_SYSTEM)
            except ProviderError as exc:
                if exc.code == ERROR_NO_TOOLS and session is None:
                    raise ExternalUnavailable(REASON_UNSUPPORTED, str(exc)) from exc
                raise
            turns.append(reply.as_turn())
            if not reply.calls:
                break
            if session is None:
                session = await ToolSession.open(self._toolbox, self._store, conv_id=conv_id,
                                                 question=question, actor="ui")
            outputs = await asyncio.gather(*(session.dispatch(c.name, dict(c.arguments))
                                             for c in reply.calls))
            turns.extend(ResultTurn(c.id, text, record.is_error)
                         for c, (record, text) in zip(reply.calls, outputs))
        if session is None:                     # 模型一个工具都没调用：照样留一条运行记录
            session = await ToolSession.open(self._toolbox, self._store, conv_id=conv_id,
                                             question=question, actor="ui")
        return session


    async def place(self, papers: Sequence[Candidate],
                    outline: Sequence[tuple[str, Sequence[str]]]) -> list[Placement]:
        """按用户自己的集合树归类引用（research 模型，一次调用）；模型不可用返回空列表。"""
        if self._llm is None or not papers or not outline:
            return []
        allowed = {p.source_id for p in papers}
        paths = {path for path, _ in outline}
        try:
            items, _calls, _tokens = await llm_json_call(
                self._llm, render_placement_prompt(outline, papers), PLACE_SYSTEM,
                lambda raw: parse_placements(raw, allowed, paths), 1)
        except (JsonContractError, RuntimeError) as exc:
            logger.warning("External placement failed: %s", exc)
            return []
        return items


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "REASON_NO_MODEL",
    "REASON_NO_TOOLS",
    "REASON_UNSUPPORTED",
    "ExternalUnavailable",
    "ToolDistributor",
    "ToolSession",
    "call_label",
]
