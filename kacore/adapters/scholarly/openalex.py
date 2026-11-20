"""OpenAlex API 适配器（adapters 层）：检索与按 DOI / PMID / OpenAlex 号查一条。

只取书目数据：标题、整名作者（仅显示）、年份、来源、类型、开放获取 PDF、参考文献、撤稿标记、
摘要（OpenAlex 以倒排索引给出，这里按位置还原成原文，是格式还原，不是改写）。
不取 OpenAlex 的主题分类、相关作品推荐等模型产出（用户要求 KA 不依赖外部模型）。

2026-02-13 起 OpenAlex 需要 API key：没有 key 时每天约 0.1 美元额度（一次检索约 0.001 美元，
2026-11-20 实测响应头）。key 由用户在应用里填写，作为 `api_key` 参数发送，不进出处账本。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from kacore.adapters.scholarly.base import (
    ENGINE_OPENALEX,
    HttpClient,
    Opener,
    ScholarlyIndex,
    year_of,
)
from kacore.domain.web_sources import PID_DOI, PID_OPENALEX, PID_PMID, IndexHit, Pid

API = "https://api.openalex.org/works"
SELECT = ",".join([
    "id", "doi", "ids", "title", "publication_year", "type", "authorships", "primary_location",
    "best_oa_location", "referenced_works", "is_retracted", "abstract_inverted_index",
    "cited_by_count",
])


class OpenAlexIndex(ScholarlyIndex):
    name = ENGINE_OPENALEX

    def __init__(self, *, api_key: str = "", opener: Opener | None = None) -> None:
        self._key = api_key.strip()
        self._http = HttpClient(ENGINE_OPENALEX, opener=opener, min_interval=0.05)

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {**extra, "select": SELECT, **({"api_key": self._key} if self._key else {})}

    def search(self, query: str, limit: int) -> list[IndexHit]:
        payload = self._http.get_json(API, self._params({"search": query, "per-page": limit}))
        works = payload.get("results") or []
        return [to_hit(w, query=query, rank=i) for i, w in enumerate(works, 1)]

    def lookup(self, pid: Pid) -> IndexHit | None:
        if pid.kind == PID_DOI:
            ref = f"doi:{pid.value}"
        elif pid.kind == PID_PMID:
            ref = f"pmid:{pid.value}"
        elif pid.kind == PID_OPENALEX:
            ref = pid.value
        else:
            return None
        url = f"{API}/{urllib.parse.quote(ref, safe=':/')}"
        try:
            payload = self._http.get_json(url, self._params({}))
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return None
            raise
        return to_hit(payload, query="", rank=0)


def abstract_from_inverted(index: dict[str, list[int]] | None) -> str:
    """OpenAlex 的 abstract_inverted_index（词 → 位置列表）按位置还原成原文。"""
    if not index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            slots[pos] = word
    return " ".join(slots[i] for i in sorted(slots))


def to_hit(work: dict[str, Any], *, query: str, rank: int) -> IndexHit:
    """一条 OpenAlex work → IndexHit（纯映射）。"""
    ids = work.get("ids") or {}
    names = [((a.get("author") or {}).get("display_name") or "")
             for a in work.get("authorships") or []]
    source = ((work.get("primary_location") or {}).get("source") or {})
    best_oa = work.get("best_oa_location") or {}
    pmid = str(ids.get("pmid") or "").rsplit("/", 1)[-1]
    fields: dict[str, object] = {
        "title": " ".join((work.get("title") or "").split()),
        "creator_names": [n for n in names if n],
        "year": year_of(work.get("publication_year")),
        "venue": source.get("display_name") or "",
        "item_type": work.get("type") or "",
        "abstract": abstract_from_inverted(work.get("abstract_inverted_index")),
        "oa_pdf_url": best_oa.get("pdf_url") or "",
        "cited_by": work.get("cited_by_count") or 0,
    }
    refs = [str(w).rsplit("/", 1)[-1] for w in work.get("referenced_works") or []]
    return IndexHit(
        engine=ENGINE_OPENALEX, query=query, rank=rank, doi=work.get("doi") or "",
        pmid=pmid, openalex=str(work.get("id") or ""), fields=fields,
        reference_ids=[f"openalex:{w}" for w in refs if w],
        retracted=bool(work.get("is_retracted")),
    )


__all__ = ["OpenAlexIndex", "abstract_from_inverted", "to_hit"]
