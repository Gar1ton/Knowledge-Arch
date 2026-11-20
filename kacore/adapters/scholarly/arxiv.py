"""arXiv API 适配器（adapters 层）：检索与按 arXiv 号查一条。

arXiv 给的是预印本：整名作者（不拆分，只放 creator_names）、摘要、PDF 地址（开放获取）。
作者若在 arXiv 登记了正式发表的 DOI，会出现在 `arxiv:doi` 里，用来与期刊版归并。
arXiv 的使用条款要求请求间隔不少于 3 秒。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from kacore.adapters.scholarly.base import (
    ENGINE_ARXIV,
    HttpClient,
    Opener,
    ScholarlyIndex,
    ScholarlyIndexError,
    year_of,
)
from kacore.domain.web_sources import PID_ARXIV, IndexHit, Pid, normalize_arxiv

API = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


class ArxivIndex(ScholarlyIndex):
    name = ENGINE_ARXIV

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient(ENGINE_ARXIV, opener=opener, min_interval=3.0)

    def search(self, query: str, limit: int) -> list[IndexHit]:
        raw = self._http.get_bytes(API, {"search_query": f"all:{query}", "max_results": limit})
        return parse_feed(raw, query=query)

    def lookup(self, pid: Pid) -> IndexHit | None:
        if pid.kind != PID_ARXIV:
            return None
        hits = parse_feed(self._http.get_bytes(API, {"id_list": pid.value}), query="")
        # arXiv 对不存在的编号返回一条只有 id 的错误条目，没有标题。
        return next((h for h in hits if h.fields.get("title") and h.arxiv), None)


def parse_feed(raw: bytes, *, query: str) -> list[IndexHit]:
    """Atom feed → IndexHit 列表（纯映射）。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ScholarlyIndexError(ENGINE_ARXIV, f"invalid Atom: {exc}") from exc
    hits: list[IndexHit] = []
    for rank, entry in enumerate(root.findall("a:entry", _NS), start=1):
        arxiv_id = normalize_arxiv(entry.findtext("a:id", "", _NS).rsplit("/abs/", 1)[-1])
        names = [" ".join(a.findtext("a:name", "", _NS).split())
                 for a in entry.findall("a:author", _NS)]
        pdf = next((ln.get("href", "") for ln in entry.findall("a:link", _NS)
                    if ln.get("title") == "pdf"), "")
        fields: dict[str, object] = {
            "title": " ".join(entry.findtext("a:title", "", _NS).split()),
            "creator_names": [n for n in names if n],
            "year": year_of(entry.findtext("a:published", "", _NS)),
            "venue": "arXiv",
            "item_type": "preprint",
            "abstract": " ".join(entry.findtext("a:summary", "", _NS).split()),
            "oa_pdf_url": pdf,
            "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
            "journal_ref": entry.findtext("x:journal_ref", "", _NS).strip(),
        }
        hits.append(IndexHit(
            engine=ENGINE_ARXIV, query=query, rank=rank, arxiv=arxiv_id,
            doi=entry.findtext("x:doi", "", _NS).strip(), fields=fields,
        ))
    return hits


__all__ = ["ArxivIndex", "parse_feed"]
