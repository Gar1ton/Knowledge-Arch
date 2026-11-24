"""Crossref 检索工具（adapters 层）：期刊、会议论文与图书的书目数据。

Crossref 是 DOI 的登记机构之一，作者带结构化的 family / given，撤稿记录在 `updated-by` 里。
不需要 key；联系邮箱（polite pool）只有用户在设置里填了才随请求发送。
"""
from __future__ import annotations

import html
import re
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
from kacore.domain.tool_calls import Block, ToolResult, ToolSetting, ToolSpec

API = "https://api.crossref.org/works"
SELECT = ",".join([
    "DOI", "title", "author", "issued", "type", "container-title", "abstract",
    "is-referenced-by-count", "updated-by", "URL", "ISBN",
])
_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefSearch(Tool):
    spec = ToolSpec(
        name="crossref_search",
        description=(
            "Search Crossref for journal articles, conference papers and books. Returns title, "
            "authors, year, venue, DOI, retraction notices and the abstract when the publisher "
            "deposited one."
        ),
        input_schema=search_schema("Bibliographic keywords: topic words, author names, title."),
        settings=(ToolSetting("contact_email"),),
    )

    def __init__(self, *, contact_email: str = "", opener: Opener | None = None) -> None:
        self._mailto = contact_email.strip()
        self._http = HttpClient("crossref", opener=opener, min_interval=0.1)

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        query, limit = query_args(arguments)
        params = {"query.bibliographic": query, "rows": limit, "select": SELECT,
                  **({"mailto": self._mailto} if self._mailto else {})}
        payload = await fetch_json(self._http, API, params)
        items = (payload.get("message") or {}).get("items") or []
        return ToolResult([to_block(i) for i in items]) if items else no_results(query)


def _plain(value: object) -> str:
    """Crossref 的文字字段夹着出版商的标记：`<i>…</i>`、`<jats:p>` 与 `&amp;` 之类的转义。
    先去标签再还原转义，得到读者看到的文字；核验比对的就是这块文字，模型照抄 `&` 才能通过。"""
    return html.unescape(_TAG_RE.sub(" ", str(value or "")))


def to_block(item: dict[str, Any]) -> Block:
    """一条 Crossref work → 一块文本（纯映射）。"""
    authors = [_plain(f"{a['family']}, {a.get('given', '')}").strip().rstrip(",")
               for a in item.get("author") or [] if a.get("family")]
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    retracted = any((u.get("type") or "").lower() == "retraction"
                    for u in item.get("updated-by") or [])
    return record_block([
        ("Title", _plain((item.get("title") or [""])[0])),
        ("Authors", authors),
        ("Year", year_of(parts[0] if parts else "")),
        ("Venue", _plain((item.get("container-title") or [""])[0])),
        ("Type", item.get("type") or ""),
        ("DOI", item.get("DOI") or ""),
        ("ISBN", item.get("ISBN") or []),
        ("URL", item.get("URL") or ""),
        ("Cited by", item.get("is-referenced-by-count") or ""),
        ("Retracted", "yes" if retracted else ""),
        ("Abstract", _plain(item.get("abstract"))),
    ])


__all__ = ["CrossrefSearch", "to_block"]
