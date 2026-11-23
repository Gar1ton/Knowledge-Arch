"""arXiv 检索工具（adapters 层）：预印本的书目数据、摘要与 PDF 地址（开放获取）。

arXiv 的使用条款要求请求间隔不少于 3 秒。作者是整名，原样给出。
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

from kacore.adapters.tools.base import (
    HttpClient,
    Opener,
    Tool,
    ToolError,
    no_results,
    query_args,
    record_block,
    search_schema,
    year_of,
)
from kacore.domain.tool_calls import Block, ToolResult, ToolSpec

API = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


class ArxivSearch(Tool):
    spec = ToolSpec(
        name="arxiv_search",
        description=(
            "Search arXiv preprints. Returns title, authors, year, abstract, the arXiv id, the "
            "open-access PDF link and the DOI of the published version when the authors added one."
        ),
        input_schema=search_schema("Keywords in English; arXiv matches title and abstract."),
    )

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient("arxiv", opener=opener, min_interval=3.0)

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        query, limit = query_args(arguments)
        raw = await asyncio.to_thread(self._http.get_bytes, API,
                                      {"search_query": f"all:{query}", "max_results": limit})
        blocks = parse_feed(raw)
        return ToolResult(blocks) if blocks else no_results(query)


def parse_feed(raw: bytes) -> list[Block]:
    """Atom feed → 每条一块文本（纯映射）。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ToolError(f"arxiv: invalid Atom ({exc})") from exc
    blocks = []
    for entry in root.findall("a:entry", _NS):
        title = entry.findtext("a:title", "", _NS)
        if not title.strip():
            continue
        abs_url = entry.findtext("a:id", "", _NS).strip()
        pdf = next((ln.get("href", "") for ln in entry.findall("a:link", _NS)
                    if ln.get("title") == "pdf"), "")
        blocks.append(record_block([
            ("Title", title),
            ("Authors", [a.findtext("a:name", "", _NS) for a in entry.findall("a:author", _NS)]),
            ("Year", year_of(entry.findtext("a:published", "", _NS))),
            ("arXiv", abs_url.rsplit("/abs/", 1)[-1]),
            ("DOI", entry.findtext("x:doi", "", _NS)),
            ("Journal reference", entry.findtext("x:journal_ref", "", _NS)),
            ("URL", abs_url),
            ("Open-access PDF", pdf),
            ("Abstract", entry.findtext("a:summary", "", _NS)),
        ]))
    return blocks


__all__ = ["ArxivSearch", "parse_feed"]
