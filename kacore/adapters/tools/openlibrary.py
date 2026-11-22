"""Open Library 检索工具（adapters 层）：图书的书目数据。免费、不需要 key。

只给书目：书名、作者（整名）、首次出版年、出版社、ISBN。Open Library 通常没有摘要。
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
from kacore.domain.tool_calls import Block, ToolResult, ToolSpec

API = "https://openlibrary.org/search.json"
FIELDS = "key,title,subtitle,author_name,first_publish_year,isbn,publisher"
# 一部作品常有上百个版次的 ISBN；给模型看前几个就够（假设值）。
_ISBNS_SHOWN = 5


class OpenLibrarySearch(Tool):
    spec = ToolSpec(
        name="openlibrary_search",
        description=(
            "Search Open Library for books. Returns title, authors, first publication year, "
            "publishers and ISBNs. Books rarely have abstracts here."
        ),
        input_schema=search_schema("Book title, author name or subject words."),
    )

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient("openlibrary", opener=opener, min_interval=0.5)

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        query, limit = query_args(arguments)
        payload = await fetch_json(self._http, API, {"q": query, "limit": limit, "fields": FIELDS})
        docs = payload.get("docs") or []
        return ToolResult([to_block(d) for d in docs]) if docs else no_results(query)


def to_block(doc: dict[str, Any]) -> Block:
    """search.json 的一条 doc → 一块文本（纯映射）。"""
    title, subtitle = str(doc.get("title") or ""), str(doc.get("subtitle") or "")
    key = str(doc.get("key") or "")
    return record_block([
        ("Title", f"{title}: {subtitle}" if title and subtitle else title),
        ("Authors", doc.get("author_name") or []),
        ("Year", year_of(doc.get("first_publish_year"))),
        ("Publisher", (doc.get("publisher") or [])[:2]),
        ("ISBN", (doc.get("isbn") or [])[:_ISBNS_SHOWN]),
        ("URL", f"https://openlibrary.org{key}" if key.startswith("/") else ""),
    ])


__all__ = ["OpenLibrarySearch", "to_block"]
