"""联网补充编排（ExternalEvidence）的契约测试：内存索引 + 假模型 + 假 Rerank，不联网。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kacore.adapters.scholarly.memory import MemoryIndex
from kacore.config import EXTERNAL_DEPTH_ABSTRACT, EXTERNAL_DEPTH_FULLTEXT, ExternalEvidenceConfig
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.web_sources import IndexHit, Trust
from kacore.managers.external_evidence import (
    ExternalEvidence,
    ExternalEvidenceUnavailable,
    IndexSet,
    passage_chunk,
    source_entry,
)
from kacore.managers.external_fulltext import FulltextCache
from kacore.repository.reranker.base import Reranker, ScoredChunk
from kacore.repository.source_store.memory import InMemorySourceDocumentStore


class _FakeLLM:
    """按 system prompt 区分两次调用；replies 为 None 时抛 RuntimeError（模拟模型不可用）。"""

    def __init__(self, plan: dict | None, select: dict | None) -> None:
        self._plan, self._select = plan, select
        self.prompts: list[str] = []

    async def generate_result(self, prompt: str, *, system_prompt: str = "",
                              allow_mock: bool = True) -> GenerationResult:
        self.prompts.append(prompt)
        reply = self._plan if "plan literature searches" in system_prompt else self._select
        if reply is None:
            raise RuntimeError("model unavailable")
        return GenerationResult(json.dumps(reply))


class _LastFirstReranker(Reranker):
    """把最后一段排到最前，便于断言确实用了 Rerank 选段。"""

    async def rerank(self, query, candidates, *, top_n=None):
        ordered = list(reversed(candidates))
        return [ScoredChunk(c, 1.0 / (i + 1)) for i, c in enumerate(ordered)][: top_n or None]


class _FakeCache(FulltextCache):
    def __init__(self, pdf_path: Path | None) -> None:
        super().__init__(Path("/nonexistent"), max_bytes=1)
        self._pdf = pdf_path

    def fetch(self, url: str):
        from kacore.managers.external_fulltext import CachedPdf

        return CachedPdf(self._pdf, "sha-x", 1) if self._pdf else None


def _hit(**kw) -> IndexHit:
    fields = kw.pop("fields", {})
    return IndexHit(engine="", query="", rank=0, fields=fields, **kw)


def _manager(llm, *, depth=EXTERNAL_DEPTH_ABSTRACT, crossref_fail=False, pdf=None, store=None):
    crossref = MemoryIndex("crossref", fail=crossref_fail, results={
        "persona drift long dialogue": [
            _hit(doi="10.1234/drift", fields={"title": "Persona Drift", "creators": ["Gao, Li"],
                                              "year": "2025", "abstract": "Drift grows."}),
            _hit(doi="10.1234/retracted", retracted=True,
                 fields={"title": "Bad", "abstract": "x"}),
        ]})
    arxiv = MemoryIndex("arxiv", results={
        "persona drift long dialogue": [
            _hit(arxiv="2601.00001", fields={"title": "Situated Traits", "abstract": "Traits.",
                                             "creator_names": ["Ana Lin"],
                                             "oa_pdf_url": "https://arxiv.org/pdf/2601.00001"}),
        ]})
    openalex = MemoryIndex("openalex", records={
        "doi:10.1234/drift": _hit(doi="10.1234/drift", reference_ids=["openalex:W7"])})
    datacite = MemoryIndex("datacite", records={
        "arxiv:2601.00001": _hit(arxiv="2601.00001", fields={"creators": ["Lin, Ana"]})})
    indexes = IndexSet(search=[crossref, arxiv],
                       lookup={"crossref": crossref, "arxiv": arxiv, "openalex": openalex,
                               "datacite": datacite})
    cfg = ExternalEvidenceConfig(depth=depth, max_sources=3)
    return ExternalEvidence(
        config_provider=lambda: cfg, index_factory=lambda _c: indexes,
        store=store or InMemorySourceDocumentStore(), llm_adapter=llm,
        reranker=_LastFirstReranker(), fulltext=_FakeCache(pdf),
    ), indexes


async def test_collect_plans_searches_verifies_and_selects() -> None:
    llm = _FakeLLM({"gaps": ["recent studies"], "queries": ["persona drift long dialogue"]},
                   {"selected": [{"id": "doi:10.1234/drift", "reason": "measures drift"},
                                 {"id": "doi:10.1234/retracted"}, {"id": "made-up"}]})
    manager, indexes = _manager(llm)
    bundle = await manager.collect("情境如何影响人格表达？", [("Mischel and Shoda", "CAPS ...")])

    assert bundle.queries == ["persona drift long dialogue"] and bundle.gaps == ["recent studies"]
    assert bundle.trust["doi:10.1234/drift"] is Trust.VERIFIED       # OpenAlex 按编号查到
    assert bundle.trust["doi:10.1234/retracted"] is Trust.RETRACTED
    assert bundle.trust["arxiv:2601.00001"] is Trust.VERIFIED         # DataCite 按编号查到
    assert bundle.selected == ["doi:10.1234/drift"]                    # 撤稿与编造 id 被丢弃
    assert bundle.reasons == {"doi:10.1234/drift": "measures drift"}
    assert [(p.source_id, p.page) for p in bundle.passages] == [("doi:10.1234/drift", None)]
    drift = bundle.record("doi:10.1234/drift")
    assert drift is not None and drift.reference_ids == {"openalex:W7"}
    assert "CAPS" in llm.prompts[0]                                    # 库内证据交给模型看
    lookups = [c for c in indexes.lookup["openalex"].calls if c[0] == "lookup"]
    assert lookups == [("lookup", "doi:10.1234/drift")]


async def test_model_unavailable_falls_back_to_question_and_index_order() -> None:
    manager, indexes = _manager(_FakeLLM(None, None))
    bundle = await manager.collect("persona drift long dialogue", [])
    assert bundle.queries == ["persona drift long dialogue"]
    assert bundle.selected == ["doi:10.1234/drift", "arxiv:2601.00001"]


async def test_agent_queries_skip_both_model_calls() -> None:
    llm = _FakeLLM({"queries": ["unused"]}, {"selected": []})
    manager, _ = _manager(llm)
    bundle = await manager.collect("q", [], queries=["persona drift long dialogue"],
                                   use_model=False)
    assert llm.prompts == []
    assert bundle.selected[:1] == ["doi:10.1234/drift"]


async def test_one_failing_index_is_recorded_but_not_fatal() -> None:
    manager, _ = _manager(_FakeLLM(None, None), crossref_fail=True)
    bundle = await manager.collect("persona drift long dialogue", [])
    assert "crossref" in bundle.errors
    assert bundle.selected == ["arxiv:2601.00001"]


async def test_no_enabled_index_raises() -> None:
    manager = ExternalEvidence(
        config_provider=ExternalEvidenceConfig, index_factory=lambda _c: IndexSet([], {}),
        store=InMemorySourceDocumentStore(), llm_adapter=None, reranker=None,
        fulltext=_FakeCache(None))
    with pytest.raises(ExternalEvidenceUnavailable):
        await manager.collect("q", [])


async def test_fulltext_passages_come_from_pdf_pages_picked_by_rerank(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 520, 760),
                            f"Page {i + 1}. " + "situated trait expression " * 30)
    doc.save(pdf)
    manager, _ = _manager(_FakeLLM(None, {"selected": [{"id": "arxiv:2601.00001"}]}),
                          depth=EXTERNAL_DEPTH_FULLTEXT, pdf=pdf)
    bundle = await manager.collect("persona drift long dialogue", [])
    assert [(p.source_id, p.page, p.file_sha256) for p in bundle.passages][:1] == [
        ("arxiv:2601.00001", 3, "sha-x")]


async def test_persist_writes_ledger_with_is_new_across_answers() -> None:
    store = InMemorySourceDocumentStore()
    manager, _ = _manager(_FakeLLM(None, None), store=store)
    first = await manager.collect("persona drift long dialogue", [])
    await manager.persist(first, conv_id="conv", msg_idx=1, actor="ui", cited=first.passages[:1])
    second = await manager.collect("persona drift long dialogue", [])
    await manager.persist(second, conv_id="conv", msg_idx=3, actor="ui", cited=[])

    hits = await store.list_capture_hits([first.capture_id, second.capture_id])
    assert {h.is_new for h in hits if h.capture_id == first.capture_id} == {True}
    assert {h.is_new for h in hits if h.capture_id == second.capture_id} == {False}
    [cite] = await store.list_citations(first.capture_id)
    assert cite.source_id == "doi:10.1234/drift" and cite.page is None
    stored = await store.get_sources(["doi:10.1234/retracted"])
    assert stored["doi:10.1234/retracted"].trust == "retracted"       # 查到未引用的也记账


async def test_source_entry_and_chunk_match_citation_contract() -> None:
    manager, _ = _manager(_FakeLLM(None, None))
    bundle = await manager.collect("persona drift long dialogue", [])
    passage = bundle.passages[0]
    entry = source_entry(bundle, passage, 7)
    assert entry["n"] == 7 and entry["origin"] == "external"
    assert entry["creators"] == ["Gao, Li"] and entry["doi"] == "10.1234/drift"
    assert entry["doc_id"] == passage_chunk(passage, 0).doc_id == "ext:doi:10.1234/drift"
