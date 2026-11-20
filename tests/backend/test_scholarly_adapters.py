"""学术索引适配器的翻译契约测试：注入假的 HTTP 打开函数，不联网。"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from kacore.adapters.scholarly.arxiv import ArxivIndex
from kacore.adapters.scholarly.base import HttpClient, ScholarlyIndexError
from kacore.adapters.scholarly.crossref import CrossrefIndex
from kacore.adapters.scholarly.memory import MemoryIndex
from kacore.adapters.scholarly.openalex import OpenAlexIndex, abstract_from_inverted
from kacore.adapters.scholarly.registries import DataCiteIndex, DoiResolver
from kacore.adapters.scholarly.semantic_scholar import SemanticScholarIndex
from kacore.domain.web_sources import IndexHit, Pid


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _opener(routes: dict[str, Any], seen: list[str]) -> Callable[..., _Resp]:
    """按 URL 前缀返回 JSON / 字节；值为 int 时抛该 HTTP 状态。"""

    def open_(request: Any, timeout: float = 0) -> _Resp:
        url = request.full_url
        seen.append(url)
        for prefix, body in routes.items():
            if url.startswith(prefix):
                if isinstance(body, int):
                    raise urllib.error.HTTPError(url, body, "err", {}, None)  # type: ignore[arg-type]
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return _Resp(raw)
        raise AssertionError(f"unexpected url {url}")

    return open_


def test_crossref_search_maps_structured_authors_refs_and_retraction() -> None:
    seen: list[str] = []
    payload = {"message": {"items": [{
        "DOI": "10.1234/ABC", "title": ["Persona  Drift"], "type": "proceedings-article",
        "author": [{"family": "Gao", "given": "Li"}, {"given": "NoFamily"}],
        "issued": {"date-parts": [[2025, 7]]}, "container-title": ["Findings of ACL"],
        "abstract": "<jats:p>Drift <jats:italic>grows</jats:italic>.</jats:p>",
        "reference": [{"DOI": "10.9999/REF"}, {"unstructured": "x"}],
        "updated-by": [{"type": "retraction", "source": "retraction-watch"}],
        "is-referenced-by-count": 18, "URL": "https://doi.org/10.1234/abc",
    }]}}
    index = CrossrefIndex(opener=_opener({"https://api.crossref.org/works?": payload}, seen))
    [hit] = index.search("persona drift", 5)
    assert hit.engine == "crossref" and hit.rank == 1 and hit.doi == "10.1234/ABC"
    assert hit.fields["creators"] == ["Gao, Li"]
    assert hit.fields["year"] == "2025"
    assert hit.fields["abstract"] == "Drift grows ."
    assert hit.reference_ids == ["doi:10.9999/ref"]
    assert hit.retracted is True
    assert "mailto" not in seen[0]                      # 用户没填邮箱就不发


def test_crossref_lookup_returns_none_on_404_and_sends_mailto_when_set() -> None:
    seen: list[str] = []
    index = CrossrefIndex(mailto="me@example.org",
                          opener=_opener({"https://api.crossref.org/works/": 404}, seen))
    assert index.lookup(Pid("doi", "10.1234/missing")) is None
    assert "mailto=me%40example.org" in seen[0]
    assert index.lookup(Pid("arxiv", "2601.00001")) is None   # 不认 arXiv 号


ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry>
  <id>http://arxiv.org/abs/2601.00001v2</id>
  <published>2026-01-03T00:00:00Z</published>
  <title>Situated   Trait Expression</title>
  <summary> We study traits. </summary>
  <author><name>Ana Lin</name></author><author><name>Bo Xu</name></author>
  <link title="pdf" href="https://arxiv.org/pdf/2601.00001v2"/>
  <arxiv:doi>10.1234/journal.1</arxiv:doi>
 </entry>
</feed>"""


def test_arxiv_keeps_full_names_unsplit_and_gives_pdf() -> None:
    seen: list[str] = []
    index = ArxivIndex(opener=_opener({"https://export.arxiv.org/api/query": ATOM}, seen))
    index._http._gate._min = 0                          # 测试里不等 3 秒
    [hit] = index.search("traits", 3)
    assert hit.arxiv == "2601.00001" and hit.doi == "10.1234/journal.1"
    assert hit.fields["creator_names"] == ["Ana Lin", "Bo Xu"]
    assert "creators" not in hit.fields                 # 整名不拆，不冒充结构化姓氏
    assert hit.fields["oa_pdf_url"] == "https://arxiv.org/pdf/2601.00001v2"
    assert hit.fields["year"] == "2026"


def test_openalex_maps_ids_refs_and_rebuilds_abstract_without_topics() -> None:
    seen: list[str] = []
    work = {
        "id": "https://openalex.org/W42", "doi": "https://doi.org/10.1234/x",
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123"}, "title": "T", "type": "article",
        "publication_year": 2022, "authorships": [{"author": {"display_name": "Mario Moretti"}}],
        "primary_location": {"source": {"display_name": "J Pers"}},
        "best_oa_location": {"pdf_url": "https://oa.example/p.pdf"},
        "referenced_works": ["https://openalex.org/W7"], "is_retracted": False,
        "abstract_inverted_index": {"strong": [0], "situations": [1], "constrain": [2]},
        "primary_topic": {"display_name": "should be ignored"},
    }
    index = OpenAlexIndex(api_key="secret", opener=_opener(
        {"https://api.openalex.org/works?": {"results": [work]}}, seen))
    [hit] = index.search("situational strength", 5)
    assert (hit.doi, hit.pmid, hit.openalex) == ("https://doi.org/10.1234/x", "123",
                                                 "https://openalex.org/W42")
    assert hit.fields["abstract"] == "strong situations constrain"
    assert hit.reference_ids == ["openalex:W7"]
    assert "topic" not in json.dumps(hit.fields)
    assert "api_key=secret" in seen[0]
    assert "api_key" not in HttpClient.request_url(seen[0].split("?")[0],
                                                   dict(urllib.parse.parse_qsl(seen[0].split("?")[1])))


def test_abstract_from_inverted_handles_empty() -> None:
    assert abstract_from_inverted(None) == ""


def test_semantic_scholar_sends_key_header_and_maps_external_ids() -> None:
    seen: list[str] = []
    headers: list[dict[str, str]] = []
    base = _opener({"https://api.semanticscholar.org/": {"data": [{
        "title": "P", "year": 2024, "authors": [{"name": "A B"}],
        "externalIds": {"DOI": "10.1234/s", "ArXiv": "2401.00002"},
        "openAccessPdf": {"url": "https://x/p.pdf"},
    }]}}, seen)

    def open_(request: Any, timeout: float = 0) -> _Resp:
        headers.append(dict(request.header_items()))
        return base(request, timeout)

    index = SemanticScholarIndex(api_key="k1", opener=open_)
    index._http._gate._min = 0
    [hit] = index.search("p", 1)
    assert hit.doi == "10.1234/s" and hit.arxiv == "2401.00002"
    assert headers[0].get("X-api-key") == "k1"


def test_datacite_gives_structured_creators_for_arxiv_ids() -> None:
    seen: list[str] = []
    record = {"data": {"attributes": {
        "doi": "10.48550/arxiv.2601.00001", "titles": [{"title": "Situated Trait Expression"}],
        "creators": [{"familyName": "Lin", "givenName": "Ana"}, {"name": "Org"}],
        "publicationYear": 2026, "publisher": "arXiv",
        "descriptions": [{"descriptionType": "Abstract", "description": "We study"}],
    }}}
    index = DataCiteIndex(opener=_opener({"https://api.datacite.org/dois/": record}, seen))
    hit = index.lookup(Pid("arxiv", "2601.00001"))
    assert hit is not None
    assert seen[0].endswith("/dois/10.48550/arxiv.2601.00001")
    assert hit.fields["creators"] == ["Lin, Ana"]
    assert hit.arxiv == "2601.00001"


def test_doi_resolver_distinguishes_missing_from_network_error() -> None:
    ok = DoiResolver(opener=_opener({"https://doi.org/api/handles/10.1234/a": {"responseCode": 1}},
                                    []))
    assert ok.exists("10.1234/a") is True
    missing = DoiResolver(opener=_opener({"https://doi.org/api/handles/": 404}, []))
    assert missing.exists("10.1234/ghost") is False
    broken = DoiResolver(opener=_opener({"https://doi.org/api/handles/": 503}, []))
    with pytest.raises(ScholarlyIndexError):
        broken.exists("10.1234/a")


def test_memory_index_rewrites_rank_and_can_fail() -> None:
    index = MemoryIndex("crossref", results={"q": [IndexHit("x", "", 9, doi="10.1234/a")]})
    [hit] = index.search("q", 5)
    assert (hit.engine, hit.query, hit.rank) == ("crossref", "q", 1)
    with pytest.raises(ScholarlyIndexError):
        MemoryIndex("arxiv", fail=True).search("q", 5)


def test_http_client_retries_once_after_429(monkeypatch: pytest.MonkeyPatch) -> None:
    import kacore.adapters.scholarly.base as base

    monkeypatch.setattr(base.time, "sleep", lambda _s: None)
    calls: list[int] = []

    def open_(request: Any, timeout: float = 0) -> _Resp:
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down",
                                         {"Retry-After": "2"}, None)  # type: ignore[arg-type]
        return _Resp(b'{"ok": true}')

    assert HttpClient("crossref", opener=open_).get_json("https://x") == {"ok": True}
    assert len(calls) == 2
