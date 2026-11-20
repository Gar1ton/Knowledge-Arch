"""Semantic Scholar Graph API 适配器（adapters 层）：检索与按 DOI / arXiv 号查一条。

只取书目数据与开放获取 PDF；不取 TLDR 摘要、推荐等模型产出（用户要求 KA 不依赖外部模型）。
需要用户在设置里填 key 才启用（以 `x-api-key` 请求头发送，不进出处账本）。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from kacore.adapters.scholarly.base import (
    ENGINE_SEMANTIC_SCHOLAR,
    HttpClient,
    Opener,
    ScholarlyIndex,
    year_of,
)
from kacore.domain.web_sources import PID_ARXIV, PID_DOI, PID_PMID, IndexHit, Pid

API = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = ",".join([
    "title", "year", "authors", "externalIds", "venue", "abstract", "openAccessPdf",
    "publicationTypes", "citationCount", "url",
])


class SemanticScholarIndex(ScholarlyIndex):
    name = ENGINE_SEMANTIC_SCHOLAR

    def __init__(self, *, api_key: str, opener: Opener | None = None) -> None:
        headers = {"x-api-key": api_key.strip()} if api_key.strip() else {}
        self._http = HttpClient(ENGINE_SEMANTIC_SCHOLAR, opener=opener, min_interval=1.0,
                                headers=headers)

    def search(self, query: str, limit: int) -> list[IndexHit]:
        payload = self._http.get_json(f"{API}/search", {"query": query, "limit": limit,
                                                        "fields": FIELDS})
        return [to_hit(p, query=query, rank=i) for i, p in enumerate(payload.get("data") or [], 1)]

    def lookup(self, pid: Pid) -> IndexHit | None:
        prefix = {PID_DOI: "DOI", PID_ARXIV: "ARXIV", PID_PMID: "PMID"}.get(pid.kind)
        if prefix is None:
            return None
        url = f"{API}/{prefix}:{urllib.parse.quote(pid.value, safe='/')}"
        try:
            payload = self._http.get_json(url, {"fields": FIELDS})
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return None
            raise
        return to_hit(payload, query="", rank=0)


def to_hit(paper: dict[str, Any], *, query: str, rank: int) -> IndexHit:
    """一条 Semantic Scholar paper → IndexHit（纯映射）。"""
    ext = paper.get("externalIds") or {}
    fields: dict[str, object] = {
        "title": " ".join((paper.get("title") or "").split()),
        "creator_names": [a.get("name") or "" for a in paper.get("authors") or [] if a.get("name")],
        "year": year_of(paper.get("year")),
        "venue": paper.get("venue") or "",
        "item_type": ",".join(paper.get("publicationTypes") or []),
        "abstract": " ".join((paper.get("abstract") or "").split()),
        "oa_pdf_url": (paper.get("openAccessPdf") or {}).get("url") or "",
        "url": paper.get("url") or "",
        "cited_by": paper.get("citationCount") or 0,
    }
    return IndexHit(
        engine=ENGINE_SEMANTIC_SCHOLAR, query=query, rank=rank, doi=str(ext.get("DOI") or ""),
        arxiv=str(ext.get("ArXiv") or ""), pmid=str(ext.get("PubMed") or ""), fields=fields,
    )


__all__ = ["SemanticScholarIndex", "to_hit"]
