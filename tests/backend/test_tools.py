"""联网工具与可追溯核验的契约测试：工具只翻译数据，工具箱统一装配与兜底，核验逐字对原文。不联网。"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from kacore.adapters.tools.base import HttpClient, Tool, ToolEnv
from kacore.adapters.tools.crossref import CrossrefSearch
from kacore.adapters.tools.openalex import OpenAlexSearch
from kacore.adapters.tools.read_open_pdf import ReadOpenPdf
from kacore.adapters.tools.registry import BUILTIN, ToolBox, builtin_specs, secret_ref
from kacore.config import ToolsConfig
from kacore.domain.tool_calls import (
    Block,
    CiteRequest,
    ToolCall,
    ToolResult,
    ToolSpec,
    trace_check,
)
from kacore.repository.reranker.base import Reranker, ScoredChunk
from kacore.settings.schema import tool_setting_specs


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = headers or {}
        self.status = 200


def _opener(*responses):
    """按顺序返回响应；元素是 bytes / (bytes, headers) / HTTPError。记录请求地址。"""
    queue = list(responses)
    seen: list[str] = []

    def opener(request, timeout=None):
        seen.append(request.full_url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        body, headers = item if isinstance(item, tuple) else (item, {})
        return _Resp(body, headers)

    opener.seen = seen  # type: ignore[attr-defined]
    return opener


def _http_error(code: int, headers: dict[str, str]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO(b""))  # type: ignore[arg-type]


# ── 工具：只翻译数据 ────────────────────────────────────────


async def test_crossref_gives_one_block_per_work_with_authors_and_retraction() -> None:
    body = json.dumps({"message": {"items": [{
        "DOI": "10.1/x", "title": ["Persona Drift"], "author": [{"family": "Gao", "given": "Li"}],
        "issued": {"date-parts": [[2025]]}, "abstract": "<jats:p>Drift grows.</jats:p>",
        "updated-by": [{"type": "retraction"}]}]}}).encode()
    tool = CrossrefSearch(contact_email="", opener=_opener(body))
    result = await tool.call({"query": "persona drift"})
    [block] = result.blocks
    assert "Authors: Gao, Li" in block.text and "Retracted: yes" in block.text
    assert "Abstract: Drift grows." in block.text and "DOI: 10.1/x" in block.text


async def test_crossref_strips_publisher_markup_so_citations_can_quote_the_plain_text() -> None:
    # 真实 Crossref 记录里的写法：标题带 <i> 且标签紧贴后一个词，期刊名带 &amp;。
    body = json.dumps({"message": {"items": [{
        "DOI": "10.1177/x", "title": ["<i>Islands</i>of Detroit: Affect &amp; Whiteness"],
        "container-title": ["Theory, Culture &amp; Society"],
        "author": [{"family": "O&#39;Neil", "given": "Ann"}]}]}}).encode()
    result = await CrossrefSearch(opener=_opener(body)).call({"query": "detroit affect"})
    [block] = result.blocks
    assert "Title: Islands of Detroit: Affect & Whiteness" in block.text
    assert "Venue: Theory, Culture & Society" in block.text and "Authors: O'Neil, Ann" in block.text
    assert "&amp;" not in block.text and "<i>" not in block.text
    call = ToolCall("r.1", "r", 1, "crossref_search", {"query": "detroit affect"}, result.blocks)
    check = trace_check(CiteRequest("c1", "Islands of Detroit",
                                    {"venue": "Theory, Culture & Society"}), {"c1": call})
    assert check.ok


async def test_openalex_stops_requesting_after_the_free_quota_is_spent() -> None:
    spent = {"X-RateLimit-Remaining-USD": "0", "X-RateLimit-Cost-USD": "0.001",
             "X-RateLimit-Reset": "3600"}
    opener = _opener((json.dumps({"results": []}).encode(), spent))
    tool = OpenAlexSearch(opener=opener, clock=lambda: 1000.0)
    await tool.call({"query": "a"})
    second = await tool.call({"query": "b"})
    assert second.is_error and "quota" in second.blocks[0].text
    assert len(opener.seen) == 1 and "paused_until" in tool.status()


def test_http_client_retries_once_after_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    opener = _opener(_http_error(429, {"Retry-After": "1"}), b"{}")
    assert HttpClient("x", opener=opener).get_json("https://x") == {}
    assert len(opener.seen) == 2


async def test_read_open_pdf_marks_each_passage_with_its_page(tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    for text in ("first page " * 40, "second page about drift " * 20):
        doc.new_page().insert_textbox(pymupdf.Rect(36, 36, 560, 800), text)
    pdf = doc.tobytes()

    class LastFirst(Reranker):
        async def rerank(self, query, candidates, *, top_n=None):
            return [ScoredChunk(c, 1.0) for c in reversed(candidates)][: top_n or None]

    tool = ReadOpenPdf(cache_dir=tmp_path, reranker=LastFirst(), opener=_opener(pdf))
    result = await tool.call({"url": "https://x/p.pdf", "query": "drift", "passages": 1})
    [block] = result.blocks
    assert (block.page, block.locator) == (2, "page 2") and "second page" in block.text


# ── 工具箱：统一装配与兜底 ──────────────────────────────────


class _Echo(Tool):
    spec = ToolSpec("echo_tool", "Echo.", {"type": "object"})

    async def call(self, arguments):
        if arguments.get("fail"):
            raise RuntimeError("boom")
        return ToolResult([Block("x" * 50), Block("y" * 50)])


def _box(cfg: ToolsConfig, secrets: dict[str, str] | None = None,
         tools: tuple[type[Tool], ...] = (_Echo,)) -> ToolBox:
    return ToolBox(config_provider=lambda: cfg,
                   read_secret=lambda ref: (secrets or {}).get(ref, ""),
                   env=ToolEnv(cache_dir=Path("/nonexistent")), tools=tools)


def test_tools_missing_a_required_setting_are_not_offered_to_the_model() -> None:
    box = _box(ToolsConfig(enabled={"semantic_scholar_search": True}), tools=BUILTIN)
    names = {s.name for s in box.specs()}
    assert "semantic_scholar_search" not in names and "crossref_search" in names
    status = {t["name"]: t for t in box.status()}
    assert status["semantic_scholar_search"]["missing"] == ["api_key"]
    keyed = _box(ToolsConfig(enabled={"semantic_scholar_search": True}),
                 {secret_ref("semantic_scholar_search", "api_key"): "k"}, tools=BUILTIN)
    assert "semantic_scholar_search" in {s.name for s in keyed.specs()}


async def test_tool_failures_become_error_results_and_are_remembered() -> None:
    box = _box(ToolsConfig())
    result = await box.call("echo_tool", {"fail": True})
    assert result.is_error and "boom" in result.blocks[0].text
    assert box.status()[0]["last_error"] == "boom"
    assert (await box.call("missing_tool", {})).is_error


async def test_results_are_clipped_to_max_result_chars() -> None:
    result = await _box(ToolsConfig(max_result_chars=70)).call("echo_tool", {})
    assert [b.text for b in result.blocks] == ["x" * 50, "y" * 20]


def test_settings_are_generated_from_tool_declarations() -> None:
    specs = {s.key: s for s in tool_setting_specs()}
    for tool in builtin_specs():
        assert f"flow.external.tools.{tool.name}.enabled" in specs
        for setting in tool.settings:
            spec = specs[f"flow.external.tools.{tool.name}.{setting.name}"]
            assert (spec.kind == "secret") == (setting.kind == "secret")
            assert not (spec.kind == "secret" and spec.target)    # 机密不进配置 dict


# ── 可追溯核验 ──────────────────────────────────────────────


def _calls() -> dict[str, ToolCall]:
    search = ToolCall("r.1", "r", 1, "search", {}, [
        Block("Title: Persona Drift\nAuthors: Gao, Li\nYear: 2025\nAbstract: Drift grows "
              "over long dialogue.")])
    pdf = ToolCall("r.2", "r", 2, "read_pdf", {}, [Block("Traits shift with cues.", "page 5", 5)])
    return {"c1": search, "c2": pdf}


def test_passage_must_appear_in_the_named_call() -> None:
    ok = trace_check(CiteRequest("c2", "traits  SHIFT with cues", {"title": "Persona Drift"}),
                     _calls())
    assert ok.ok and ok.call_id == "r.2" and ok.block == 0
    wrong_call = trace_check(CiteRequest("c1", "Traits shift with cues."), _calls())
    assert not wrong_call.ok and wrong_call.missing == ("passage",)


def test_every_field_must_appear_in_some_call_of_the_run() -> None:
    check = trace_check(CiteRequest("c1", "Drift grows", {"title": "Persona Drift",
                                                          "year": "2024",
                                                          "creators": ["Gao, Li", "Wu, Z"]}),
                        _calls())
    assert not check.ok and check.missing == ("year", "creators")


def test_curly_quotes_and_emphasis_stars_do_not_count_as_differences() -> None:
    calls = {"c1": ToolCall("r.1", "r", 1, "search", {}, [
        Block("Title: The “Turn to Affect”\nAbstract: a novel’s style; **In**terviewing agents.")])}
    check = trace_check(CiteRequest("c1", "a novel's style; Interviewing agents.",
                                    {"title": 'The "Turn to Affect"'}), calls)
    assert check.ok


def test_cite_must_point_at_an_existing_tool_call() -> None:
    assert trace_check(CiteRequest("c9", "Drift grows"), _calls()).missing == ("call",)
