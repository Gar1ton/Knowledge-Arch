"""Open Library 适配器（adapters 层）：按书检索，按 ISBN 查一条。

为何存在：人文领域大量引用专著，很多书只有 ISBN、没有 DOI；Open Library 让这些书也能被检索和
按 ISBN 核验（与 Crossref 的 ISBN 记录互查）。免费、不需要 key。

只取书目数据：书名、整名作者（Open Library 不给结构化姓名，只放 creator_names，不拆分）、
首次出版年、出版社、全部 ISBN。Open Library 通常不提供摘要，所以书一般只出现在来源栏里供核验与
入库，不会作为回答的证据片段（没有可引用的正文）。
"""
from __future__ import annotations

from typing import Any

from kacore.adapters.scholarly.base import (
    ENGINE_OPENLIBRARY,
    HttpClient,
    Opener,
    ScholarlyIndex,
    year_of,
)
from kacore.domain.web_sources import PID_ISBN, IndexHit, Pid

SEARCH_API = "https://openlibrary.org/search.json"
BOOKS_API = "https://openlibrary.org/api/books"
SEARCH_FIELDS = "key,title,subtitle,author_name,first_publish_year,isbn,publisher"
_MAX_ISBNS = 20   # 一部作品可能有上百个版次的 ISBN，留前若干个作别名就够归并


class OpenLibraryIndex(ScholarlyIndex):
    name = ENGINE_OPENLIBRARY

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient(ENGINE_OPENLIBRARY, opener=opener, min_interval=0.5)

    def search_url(self, query: str, limit: int) -> str:
        return HttpClient.request_url(SEARCH_API, {"q": query, "limit": limit})

    def search(self, query: str, limit: int) -> list[IndexHit]:
        payload = self._http.get_json(SEARCH_API, {"q": query, "limit": limit,
                                                   "fields": SEARCH_FIELDS})
        docs = payload.get("docs") or []
        return [search_hit(d, query=query, rank=i) for i, d in enumerate(docs, start=1)]

    def lookup(self, pid: Pid) -> IndexHit | None:
        if pid.kind != PID_ISBN:
            return None
        key = f"ISBN:{pid.value}"
        payload = self._http.get_json(BOOKS_API, {"bibkeys": key, "format": "json",
                                                  "jscmd": "data"})
        record = payload.get(key) if isinstance(payload, dict) else None
        return book_hit(record, isbn=pid.value) if record else None


def _title(main: str, subtitle: str) -> str:
    main, subtitle = " ".join((main or "").split()), " ".join((subtitle or "").split())
    return f"{main}: {subtitle}" if main and subtitle else main


def search_hit(doc: dict[str, Any], *, query: str, rank: int) -> IndexHit:
    """search.json 的一条 doc → IndexHit（纯映射）。"""
    key = str(doc.get("key") or "")
    fields: dict[str, object] = {
        "title": _title(str(doc.get("title") or ""), str(doc.get("subtitle") or "")),
        "creator_names": [str(n) for n in doc.get("author_name") or [] if n],
        "year": year_of(doc.get("first_publish_year")),
        "venue": ", ".join(str(p) for p in (doc.get("publisher") or [])[:2]),
        "item_type": "book",
        "url": f"https://openlibrary.org{key}" if key.startswith("/") else "",
    }
    return IndexHit(engine=ENGINE_OPENLIBRARY, query=query, rank=rank,
                    isbns=[str(i) for i in (doc.get("isbn") or [])[:_MAX_ISBNS]], fields=fields)


def book_hit(record: dict[str, Any], *, isbn: str) -> IndexHit:
    """api/books（jscmd=data）的一条记录 → IndexHit（纯映射）。"""
    ids = record.get("identifiers") or {}
    isbns = [isbn, *(ids.get("isbn_13") or []), *(ids.get("isbn_10") or [])]
    fields: dict[str, object] = {
        "title": _title(str(record.get("title") or ""), str(record.get("subtitle") or "")),
        "creator_names": [str(a.get("name")) for a in record.get("authors") or []
                          if isinstance(a, dict) and a.get("name")],
        "year": year_of(_last_year(str(record.get("publish_date") or ""))),
        "venue": ", ".join(str(p.get("name")) for p in (record.get("publishers") or [])[:2]
                           if isinstance(p, dict) and p.get("name")),
        "item_type": "book",
        "url": str(record.get("url") or ""),
    }
    return IndexHit(engine=ENGINE_OPENLIBRARY, query="", rank=0,
                    isbns=list(dict.fromkeys(isbns))[:_MAX_ISBNS], fields=fields)


def _last_year(text: str) -> str:
    """publish_date 形如 'March 2016' 或 '2016'：取其中的四位年份（格式解析）。"""
    for token in reversed(text.replace(",", " ").split()):
        if len(token) == 4 and token.isdigit():
            return token
    return ""


__all__ = ["OpenLibraryIndex", "book_hit", "search_hit"]
