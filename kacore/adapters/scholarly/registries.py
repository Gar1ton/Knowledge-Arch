"""DOI 登记机构的查询（adapters 层）：doi.org 是否认得一个 DOI，与 DataCite 的结构化书目。

- doi.org Handle API：只回答「这个 DOI 存在吗」。核验时用来把「编号查不到」（疑似编造）
  与「只是还没被第二个索引收录」区分开（`kacore.domain.web_sources.trust_level` 的 CONFLICT）。
- DataCite：arXiv 论文的 DOI（10.48550/arXiv.xxxx）、Zenodo 等由 DataCite 登记。它给出结构化的
  familyName / givenName，用来补上 arXiv 等只给整名的来源缺的作者姓氏（引用格式需要）。
两者都不需要 key。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from kacore.adapters.scholarly.base import (
    ENGINE_DATACITE,
    HttpClient,
    Opener,
    ScholarlyIndex,
    ScholarlyIndexError,
    year_of,
)
from kacore.domain.web_sources import PID_ARXIV, PID_DOI, IndexHit, Pid

HANDLE_API = "https://doi.org/api/handles"
DATACITE_API = "https://api.datacite.org/dois"
ARXIV_DOI_PREFIX = "10.48550/arxiv."


class DoiResolver:
    """问 doi.org：这个 DOI 存在吗。返回 True / False；网络错误抛 ScholarlyIndexError。"""

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient("doi.org", opener=opener, min_interval=0.05)

    def exists(self, doi: str) -> bool:
        url = f"{HANDLE_API}/{urllib.parse.quote(doi, safe='/')}"
        try:
            payload = self._http.get_json(url)
        except ScholarlyIndexError as exc:
            if exc.status == 404:
                return False
            raise
        return int(payload.get("responseCode") or 0) == 1


class DataCiteIndex(ScholarlyIndex):
    """只用于按编号查（lookup）；检索交给其它索引。arXiv 号按 arXiv 的 DataCite DOI 去查。"""

    name = ENGINE_DATACITE

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._http = HttpClient(ENGINE_DATACITE, opener=opener, min_interval=0.1)

    def search(self, query: str, limit: int) -> list[IndexHit]:
        return []

    def lookup(self, pid: Pid) -> IndexHit | None:
        if pid.kind == PID_DOI:
            doi = pid.value
        elif pid.kind == PID_ARXIV:
            doi = f"{ARXIV_DOI_PREFIX}{pid.value}"
        else:
            return None
        try:
            payload = self._http.get_json(f"{DATACITE_API}/{urllib.parse.quote(doi, safe='/')}")
        except ScholarlyIndexError as exc:
            if exc.status == 404:
                return None
            raise
        return to_hit(payload.get("data") or {}, arxiv=pid.value if pid.kind == PID_ARXIV else "")


def to_hit(data: dict[str, Any], *, arxiv: str = "") -> IndexHit:
    """一条 DataCite DOI 记录 → IndexHit（纯映射）。"""
    attrs = data.get("attributes") or {}
    creators = [
        f"{c['familyName']}, {c.get('givenName', '')}".strip().rstrip(",")
        for c in attrs.get("creators") or [] if c.get("familyName")
    ]
    titles = attrs.get("titles") or [{}]
    abstract = next((d.get("description") or "" for d in attrs.get("descriptions") or []
                     if d.get("descriptionType") == "Abstract"), "")
    fields: dict[str, object] = {
        "title": " ".join((titles[0].get("title") or "").split()),
        "creators": creators,
        "year": year_of(attrs.get("publicationYear")),
        "venue": attrs.get("publisher") or "",
        "item_type": ((attrs.get("types") or {}).get("resourceTypeGeneral") or "").lower(),
        "abstract": " ".join(abstract.split()),
        "url": attrs.get("url") or "",
    }
    return IndexHit(engine=ENGINE_DATACITE, query="", rank=0, doi=attrs.get("doi") or "",
                    arxiv=arxiv, fields=fields)


__all__ = ["ARXIV_DOI_PREFIX", "DataCiteIndex", "DoiResolver", "to_hit"]
