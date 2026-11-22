"""distributor 的契约：按模型的工具调用执行并记账，cite 逐字核对，最后一轮只给 cite，
模型不支持工具调用或没有可用工具时不建账本记录。假模型 + 假工具，不联网。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kacore.adapters.tools.base import Tool, ToolEnv
from kacore.adapters.tools.registry import ToolBox
from kacore.config import ToolsConfig
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.tool_calls import Block, ToolCallRequest, ToolResult, ToolSpec, ToolTurn
from kacore.managers.tool_distributor import (
    REASON_NO_TOOLS,
    REASON_UNSUPPORTED,
    ExternalUnavailable,
    ToolDistributor,
)
from kacore.providers.base import ERROR_NO_TOOLS, ProviderError
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

RESULT = "Title: Persona Drift\nYear: 2025\nAbstract: Drift grows over long dialogue."


class _Search(Tool):
    spec = ToolSpec("fake_search", "Search.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block(RESULT)])


class _Model:
    """按脚本逐轮返回工具调用；记录每轮收到的工具名。"""

    def __init__(self, script: list[list[tuple[str, dict[str, Any]]]],
                 error: ProviderError | None = None) -> None:
        self.script, self.error = script, error
        self.offered: list[list[str]] = []

    async def generate_with_tools(self, turns, tools, system_prompt=""):
        self.offered.append([t.name for t in tools])
        if self.error is not None:
            raise self.error
        step = self.script.pop(0) if self.script else []
        calls = tuple(ToolCallRequest(f"x{len(self.offered)}{i}", name, args)
                      for i, (name, args) in enumerate(step))
        return ToolTurn(GenerationResult(""), calls)


def _distributor(model: _Model, store: InMemorySourceDocumentStore, *, rounds: int = 4,
                 enabled: bool = True) -> ToolDistributor:
    cfg = ToolsConfig(max_rounds=rounds, enabled={"fake_search": enabled})
    box = ToolBox(config_provider=lambda: cfg, read_secret=lambda _r: "",
                  env=ToolEnv(cache_dir=Path("/nonexistent")), tools=(_Search,))
    return ToolDistributor(toolbox=box, store=store, client=model, config_provider=lambda: cfg)


CITE = {"call": "c1", "title": "Persona Drift", "year": "2025",
        "passage": "Drift grows over long dialogue."}


async def test_tool_calls_and_accepted_citation_are_recorded() -> None:
    store = InMemorySourceDocumentStore()
    model = _Model([[("fake_search", {"query": "drift"})], [("cite", CITE)], []])
    session = await _distributor(model, store).run("q", [], conv_id="conv")
    calls = await store.list_tool_calls(session.capture.capture_id)
    assert [c.tool for c in calls] == ["fake_search", "cite"]
    [citation] = await store.list_citations(session.capture.capture_id)
    assert citation.call_id == calls[0].call_id and citation.fields["year"] == "2025"
    assert len(model.offered) == 3                                  # 第三轮模型不再调用工具


async def test_rejected_cite_is_recorded_but_not_a_citation() -> None:
    store = InMemorySourceDocumentStore()
    bad = {**CITE, "year": "2024"}
    model = _Model([[("fake_search", {})], [("cite", bad)], []])
    session = await _distributor(model, store).run("q", [], conv_id="conv")
    calls = await store.list_tool_calls(session.capture.capture_id)
    assert calls[1].is_error and "year" in calls[1].blocks[0].text
    assert session.rejected == 1 and await store.list_citations(session.capture.capture_id) == []


async def test_the_last_round_offers_only_cite() -> None:
    model = _Model([[("fake_search", {})], [("fake_search", {})], [("fake_search", {})]])
    await _distributor(model, InMemorySourceDocumentStore(), rounds=3).run("q", [], conv_id="c")
    assert model.offered == [["fake_search", "cite"], ["fake_search", "cite"], ["cite"]]


async def test_model_without_tool_support_leaves_no_ledger_record() -> None:
    store = InMemorySourceDocumentStore()
    model = _Model([], error=ProviderError(ERROR_NO_TOOLS, "no tools"))
    with pytest.raises(ExternalUnavailable) as info:
        await _distributor(model, store).run("q", [], conv_id="conv")
    assert info.value.reason == REASON_UNSUPPORTED
    assert await store.list_recent_tool_calls(10) == []


async def test_no_enabled_tool_is_unavailable_without_calling_the_model() -> None:
    model = _Model([])
    with pytest.raises(ExternalUnavailable) as info:
        await _distributor(model, InMemorySourceDocumentStore(), enabled=False).run(
            "q", [], conv_id="conv")
    assert info.value.reason == REASON_NO_TOOLS and model.offered == []
