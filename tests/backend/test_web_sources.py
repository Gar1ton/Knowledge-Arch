"""domain 层外部文献标识、归并与核验规则的契约测试（纯函数，无 I/O）。"""
from __future__ import annotations

import pytest

from kacore.domain.web_sources import (
    CITABLE,
    IndexHit,
    Trust,
    canonical_id,
    clean_url,
    merge_hits,
    normalize_arxiv,
    normalize_doi,
    rank_drift,
    trust_level,
)

# ── 规整 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1037/0033-295X.102.2.246", "10.1037/0033-295x.102.2.246"),
        ("https://doi.org/10.18653/v1/2025.findings-acl.1", "10.18653/v1/2025.findings-acl.1"),
        ("doi:10.48550/arXiv.2601.01234", "10.48550/arxiv.2601.01234"),
        ("not a doi", ""),
        ("", ""),
    ],
)
def test_normalize_doi(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2601.01234v2", "2601.01234"),
        ("https://arxiv.org/abs/2604.13846v3", "2604.13846"),
        ("arXiv:2412.12144", "2412.12144"),
        ("hep-th/9901001v1", "hep-th/9901001"),
        ("W123", ""),
    ],
)
def test_normalize_arxiv(raw: str, expected: str) -> None:
    assert normalize_arxiv(raw) == expected


def test_clean_url_drops_tracking_and_fragment_only() -> None:
    url = "https://www.example.org/paper/?id=7&utm_source=x&fbclid=y&ref=z#section-2"
    assert clean_url(url) == "https://www.example.org/paper/?id=7&ref=z"


def test_canonical_id_prefers_doi_then_arxiv_then_openalex() -> None:
    both = IndexHit("crossref", "q", 1, doi="10.1234/ABC", arxiv="2601.00001")
    assert canonical_id(both) == "doi:10.1234/abc"
    assert canonical_id(IndexHit("arxiv", "q", 1, arxiv="2601.00001v3")) == "arxiv:2601.00001"
    alex = IndexHit("openalex", "q", 1, openalex="https://openalex.org/W42")
    assert canonical_id(alex) == "openalex:W42"
    web = IndexHit("web", "q", 1, url="https://a.org/x?utm_medium=m")
    assert canonical_id(web) == "url:https://a.org/x"
    assert canonical_id(IndexHit("web", "q", 1)) == ""


# ── 归并 ────────────────────────────────────────────────────


def test_merge_by_any_shared_identifier_and_record_field_origin() -> None:
    hits = [
        IndexHit("arxiv", "q1", 1, arxiv="2601.00001v2",
                 fields={"title": "T (arXiv)", "abstract": "A"}),
        IndexHit("openalex", "q1", 3, doi="10.1234/X", arxiv="2601.00001",
                 fields={"title": "T", "venue": "V"}, reference_ids=["W1", "W2"]),
        IndexHit("crossref", "q2", 2, doi="https://doi.org/10.1234/x", fields={"year": "2026"}),
    ]
    [record] = merge_hits(hits)
    assert record.pid.key == "doi:10.1234/x"            # 后来出现的 DOI 升为首选标识
    assert record.source_id == "doi:10.1234/x"
    assert record.engines == ["arxiv", "openalex", "crossref"]
    assert record.found_by == "arxiv"
    assert record.fields == {"title": "T (arXiv)", "abstract": "A", "venue": "V", "year": "2026"}
    assert record.field_origin == {
        "title": "arxiv", "abstract": "arxiv", "venue": "openalex", "year": "crossref",
    }
    assert record.reference_ids == {"W1", "W2"}


def test_merge_does_not_join_by_title() -> None:
    hits = [
        IndexHit("openalex", "q", 1, openalex="W1", fields={"title": "Same Title"}),
        IndexHit("openalex", "q", 2, openalex="W2", fields={"title": "Same Title"}),
    ]
    assert len(merge_hits(hits)) == 2


def test_merge_drops_hits_without_identifier() -> None:
    assert merge_hits([IndexHit("web", "q", 1, fields={"title": "x"})]) == []


# ── 核验 ────────────────────────────────────────────────────


def _record(**hit_kwargs: object) -> object:
    [record] = merge_hits([IndexHit(**hit_kwargs)])  # type: ignore[arg-type]
    return record


def test_single_index_is_single_until_second_lookup_confirms() -> None:
    record = _record(engine="crossref", query="q", rank=1, doi="10.1234/a")
    assert trust_level(record) is Trust.SINGLE
    record.lookup_hits.add("crossref")                  # 自己查到自己不算第二个来源
    assert trust_level(record) is Trust.SINGLE
    record.lookup_hits.add("openalex")
    assert trust_level(record) is Trust.VERIFIED


def test_second_index_returning_it_in_search_also_confirms() -> None:
    [record] = merge_hits([
        IndexHit("crossref", "q", 1, doi="10.1234/a"),
        IndexHit("openalex", "q", 4, doi="10.1234/A"),
    ])
    assert trust_level(record) is Trust.VERIFIED


def test_retracted_wins_over_everything() -> None:
    record = _record(engine="crossref", query="q", rank=1, doi="10.1234/a", retracted=True)
    record.lookup_hits.add("openalex")
    assert trust_level(record) is Trust.RETRACTED
    assert Trust.RETRACTED not in CITABLE


def test_unresolvable_doi_is_conflict_but_unchecked_is_not() -> None:
    record = _record(engine="semantic_scholar", query="q", rank=1, doi="10.1234/ghost")
    assert trust_level(record) is Trust.SINGLE           # 还没查 doi.org
    record.doi_resolves = False
    assert trust_level(record) is Trust.CONFLICT


def test_no_persistent_identifier_is_unresolved() -> None:
    record = _record(engine="openalex", query="q", rank=1, openalex="W9")
    record.lookup_hits.add("crossref")
    assert trust_level(record) is Trust.UNRESOLVED


def test_rank_drift_compares_last_two_appearances() -> None:
    order = ["c1", "c2", "c3"]
    assert rank_drift({"c1": 5, "c3": 9}, order) == (5, 9)
    assert rank_drift({"c2": 3, "c3": 3}, order) is None
    assert rank_drift({"c3": 1}, order) is None
