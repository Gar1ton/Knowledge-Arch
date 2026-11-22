"""Semantic Scholar 检索工具（adapters 层）：书目数据与开放获取 PDF。

只取书目字段；不取 TLDR、推荐等模型产出（用户要求 KA 不依赖外部模型）。需要用户填 key
（以 `x-api-key` 请求头发送，不写进账本）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kacore.adapters.tools.base import (
    HttpClient,
    Opener,
    Tool,
    fetch_json,
    no_results,
    query_args,
    record_block,
    search_schema,
    year_of,
)
from kacore.domain.tool_calls import SETTING_SECRET, Block, ToolResult, ToolSetting, ToolSpec

API = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = ",".join([
    "title", "year", "authors", "externalIds", "venue", "abstract", "openAccessPdf",
    "publicationTypes", "citationCount", "url",
])


class SemanticScholarSearch(Tool):
    spec = ToolSpec(
        name="semantic_scholar_search",
        description=(
            "Search Semantic Scholar across disciplines. Returns title, authors, year, venue, "
            "DOI / arXiv / PubMed ids, abstract and an open-access PDF link when one exists."
        ),
        input_schema=search_schema("Keywords: topic words, author names, title."),
        settings=(ToolSetting("api_key", SETTING_SECRET, required=True),),
        default_enabled=False,
    )

    def __init__(self, *, api_key: str, opener: Opener | None = None) -> None:
        self._http = HttpClient("semantic_scholar", opener=opener, min_interval=1.0,
                                headers={"x-api-key": api_key.strip()})

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        query, limit = query_args(arguments)
        payload = await fetch_json(self._http, API,
                                   {"query": query, "limit": limit, "fields": FIELDS})
        papers = payload.get("data") or []
        return ToolResult([to_block(p) for p in papers]) if papers else no_results(query)


def to_block(paper: dict[str, Any]) -> Block:
    """一条 Semantic Scholar paper → 一块文本（纯映射）。"""
    ext = paper.get("externalIds") or {}
    return record_block([
        ("Title", paper.get("title") or ""),
        ("Authors", [a.get("name") or "" for a in paper.get("authors") or []]),
        ("Year", year_of(paper.get("year"))),
        ("Venue", paper.get("venue") or ""),
        ("Type", paper.get("publicationTypes") or []),
        ("DOI", ext.get("DOI") or ""),
        ("arXiv", ext.get("ArXiv") or ""),
        ("PubMed", ext.get("PubMed") or ""),
        ("URL", paper.get("url") or ""),
        ("Open-access PDF", (paper.get("openAccessPdf") or {}).get("url") or ""),
        ("Cited by", paper.get("citationCount") or ""),
        ("Abstract", paper.get("abstract") or ""),
    ])


__all__ = ["SemanticScholarSearch", "to_block"]
