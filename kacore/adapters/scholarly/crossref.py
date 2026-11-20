"""Crossref REST API 适配器（adapters 层）：检索与按 DOI 查一条。

Crossref 是期刊 / 会议 DOI 的登记机构之一，作者带结构化的 family / given，参考文献带 DOI，
撤稿记录（含 Retraction Watch 数据）在 `updated-by` 里。不需要 key；联系邮箱（polite pool）
只有用户在设置里填了才随请求发送。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from kacore.adapters.scholarly.base import (
    ENGINE_CROSSREF,
    HttpClient,
    Opener,
    ScholarlyIndex,
    year_of,
)
from kacore.domain.web_sources import PID_DOI, IndexHit, Pid

API = "https://api.crossref.org/works"
SELECT = ",".join([
    "DOI", "title", "author", "issued", "type", "container-title", "abstract", "reference",
    "is-referenced-by-count", "updated-by", "link", "URL",
])
_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefIndex(ScholarlyIndex):
    name = ENGINE_CROSSREF

    def __init__(self, *, mailto: str = "", opener: Opener | None = None) -> None:
        self._mailto = mailto.strip()
        self._http = HttpClient(ENGINE_CROSSREF, opener=opener, min_interval=0.1)

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {**extra, **({"mailto": self._mailto} if self._mailto else {})}

    def search(self, query: str, limit: int) -> list[IndexHit]:
        params = self._params({"query.bibliographic": query, "rows": limit, "select": SELECT})
        payload = self._http.get_json(API, params)
        items = (payload.get("message") or {}).get("items") or []
        return [to_hit(item, query=query, rank=i) for i, item in enumerate(items, start=1)]

    def lookup(self, pid: Pid) -> IndexHit | None:
        if pid.kind != PID_DOI:
            return None
        url = f"{API}/{urllib.parse.quote(pid.value, safe='/')}"
        try:
            payload = self._http.get_json(url, self._params({}))
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return None
            raise
        return to_hit(payload.get("message") or {}, query="", rank=0)


def to_hit(item: dict[str, Any], *, query: str, rank: int) -> IndexHit:
    """一条 Crossref work → IndexHit（纯映射）。"""
    creators = [
        f"{a['family']}, {a.get('given', '')}".strip().rstrip(",")
        for a in item.get("author") or [] if a.get("family")
    ]
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    pdf = next((ln.get("URL") for ln in item.get("link") or []
                if ln.get("content-type") == "application/pdf"), "")
    fields: dict[str, object] = {
        "title": " ".join(((item.get("title") or [""])[0]).split()),
        "creators": creators,
        "year": year_of(parts[0] if parts else ""),
        "venue": (item.get("container-title") or [""])[0],
        "item_type": item.get("type") or "",
        "abstract": " ".join(_TAG_RE.sub(" ", item.get("abstract") or "").split()),
        "url": item.get("URL") or "",
        "cited_by": item.get("is-referenced-by-count") or 0,
        "publisher_pdf_url": pdf or "",   # 出版社给的 PDF 链接，不一定开放获取
    }
    refs = [f"doi:{r['DOI'].lower()}" for r in item.get("reference") or [] if r.get("DOI")]
    retracted = any((u.get("type") or "").lower() == "retraction"
                    for u in item.get("updated-by") or [])
    return IndexHit(
        engine=ENGINE_CROSSREF, query=query, rank=rank, doi=item.get("DOI") or "",
        fields=fields, reference_ids=refs, retracted=retracted,
    )


__all__ = ["CrossrefIndex", "to_hit"]
