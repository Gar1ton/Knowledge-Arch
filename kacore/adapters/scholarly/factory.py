"""按用户设置组装一次联网补充可用的索引（adapters 层；由组合根注入到 ExternalEvidence）。

检索只用用户启用的索引；核验另外总是可用 DataCite 与 doi.org（只回答「编号存在吗」与结构化作者，
不需要 key）。OpenAlex / Semantic Scholar 的 key 每次现读（换 key 不用重启）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from kacore.adapters.scholarly.arxiv import ArxivIndex
from kacore.adapters.scholarly.base import ScholarlyIndex
from kacore.adapters.scholarly.crossref import CrossrefIndex
from kacore.adapters.scholarly.openalex import OpenAlexIndex
from kacore.adapters.scholarly.openlibrary import OpenLibraryIndex
from kacore.adapters.scholarly.registries import DataCiteIndex, DoiResolver
from kacore.adapters.scholarly.semantic_scholar import SemanticScholarIndex
from kacore.config import OPENALEX_KEY_SECRET, SEMANTIC_SCHOLAR_KEY_SECRET

if TYPE_CHECKING:
    from kacore.config import ExternalEvidenceConfig
    from kacore.managers.external_evidence import IndexSet


def build_index_set(cfg: ExternalEvidenceConfig, read_secret: Callable[[str], str]) -> IndexSet:
    from kacore.managers.external_evidence import IndexSet

    search: list[ScholarlyIndex] = []
    if cfg.crossref:
        search.append(CrossrefIndex(mailto=cfg.contact_email))
    if cfg.openalex:
        search.append(OpenAlexIndex(api_key=_safe(read_secret, OPENALEX_KEY_SECRET)))
    if cfg.arxiv:
        search.append(ArxivIndex())
    if cfg.openlibrary:
        search.append(OpenLibraryIndex())
    s2_key = _safe(read_secret, SEMANTIC_SCHOLAR_KEY_SECRET)
    if cfg.semantic_scholar and s2_key:
        search.append(SemanticScholarIndex(api_key=s2_key))
    lookup: dict[str, ScholarlyIndex] = {i.name: i for i in search}
    lookup.setdefault("datacite", DataCiteIndex())
    return IndexSet(search=search, lookup=lookup, resolver=DoiResolver())


def _safe(read_secret: Callable[[str], str], name: str) -> str:
    try:
        return (read_secret(name) or "").strip()
    except Exception:  # noqa: BLE001 - 机密库不可用时按「没填」处理
        return ""


__all__ = ["build_index_set"]
