"""ask(external=True) 的端到端契约：外部证据续接编号、Harvard 引用、聊天记录摘要与出处账本。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from static_retriever import StaticRetriever

from kacore.adapters.scholarly.memory import MemoryIndex
from kacore.api import KnowledgeRepositoryApi
from kacore.config import EXTERNAL_DEPTH_ABSTRACT, ExternalEvidenceConfig
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.domain.web_sources import IndexHit
from kacore.managers.external_evidence import ExternalEvidence, IndexSet
from kacore.managers.external_fulltext import FulltextCache
from kacore.repository.source_store.memory import InMemorySourceDocumentStore


class _SynthLLM:
    """合成回答：引用库内 [1] 与外部 [2]；记录收到的提示词。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_result(self, prompt: str, system_prompt: str = "", **_kw):
        self.prompts.append(prompt)
        return GenerationResult(text="Situations constrain traits [1]. Drift grows [2].")


def _external(store: InMemorySourceDocumentStore) -> ExternalEvidence:
    crossref = MemoryIndex("crossref", results={"alpha": [IndexHit(
        "", "", 0, doi="10.1234/drift",
        fields={"title": "Persona Drift", "creators": ["Gao, Li"], "year": "2025",
                "abstract": "Drift grows over long dialogue."})]})
    openalex = MemoryIndex("openalex", records={"doi:10.1234/drift": IndexHit(
        "openalex", "", 0, doi="10.1234/drift")})
    indexes = IndexSet(search=[crossref], lookup={"crossref": crossref, "openalex": openalex})
    return ExternalEvidence(
        config_provider=lambda: ExternalEvidenceConfig(depth=EXTERNAL_DEPTH_ABSTRACT),
        index_factory=lambda _c: indexes, store=store, llm_adapter=None, reranker=None,
        fulltext=FulltextCache(Path("/nonexistent"), max_bytes=1),
    )


async def _api(store: InMemorySourceDocumentStore, llm: _SynthLLM) -> KnowledgeRepositoryApi:
    await store.add_document(SourceDocument(
        doc_id="d1", title="Mischel and Shoda (1995) - A Cognitive-Affective System Theory.pdf",
        file_path="/d1.pdf", content_type="application/pdf", size_bytes=1, content_hash="h",
        collection="c1"))
    retriever = StaticRetriever({"c1": [DocumentChunk("k1", "d1", 0, "alpha situations", "h")]})
    return KnowledgeRepositoryApi(source_store=store, retrieval_orchestrator=retriever,
                                  llm_adapter=llm)  # type: ignore[arg-type]


async def test_external_evidence_is_cited_and_recorded() -> None:
    store = InMemorySourceDocumentStore()
    llm = _SynthLLM()
    api = await _api(store, llm)
    api.attach_external_evidence(_external(store))

    result = await api.ask(question="alpha", collection="c1", external=True)

    assert "[2] Persona Drift (abstract) [external]" in llm.prompts[-1]
    local, ext = result["sources"]
    assert local["n"] == 1 and local["origin"] != "external"
    assert ext["n"] == 2 and ext["origin"] == "external" and ext["trust"] == "verified"
    assert "Gao" in ext["harvard_in_text"] and "2025" in ext["harvard_in_text"]
    assert "(Gao, 2025)" in result["answer"]
    summary = result["external"]
    assert summary["status"] == "ok" and summary["cited"] == 1 and summary["used"] == 1

    cid = result["conversation_id"]
    messages = await store.get_chat_messages(cid)
    assert messages[-1]["meta"]["external"]["capture_id"] == summary["capture_id"]
    [capture] = await store.list_captures(cid)
    assert capture.msg_idx == len(messages) - 1
    [citation] = await store.list_citations(capture.capture_id)
    assert citation.source_id == "doi:10.1234/drift" and citation.page is None


async def test_external_without_manager_still_answers_from_library() -> None:
    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    result = await api.ask(question="alpha", collection="c1", external=True)
    assert result["external"]["status"] == "unavailable"
    assert [s["n"] for s in result["sources"]] == [1]
    assert result["answer"]


async def test_external_off_by_default() -> None:
    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    api.attach_external_evidence(_external(store))
    result = await api.ask(question="alpha", collection="c1")
    assert result["external"] is None
    assert await store.list_captures(result["conversation_id"]) == []


# ── AI 助手经 skill 取外部证据 ────────────────────────────────


async def test_agent_external_evidence_uses_given_queries_without_model() -> None:
    store = InMemorySourceDocumentStore()
    llm = _SynthLLM()
    api = await _api(store, llm)
    api.attach_external_evidence(_external(store))

    result = await api.collect_external_for_agent(question="q", queries=["alpha"])

    assert llm.prompts == []                                # KA 不调用模型
    assert result["plugin_llm_used"] is False
    [entry] = result["evidence"]
    assert entry["harvard_in_text"] == "Gao, 2025" and entry["trust"] == "verified"
    assert result["candidates"][0]["source_id"] == "doi:10.1234/drift"
    today = datetime.now(timezone.utc).date().isoformat()
    [capture] = await store.list_captures(f"agent-{today}")
    assert capture.actor == "agent" and capture.capture_id == result["capture_id"]


async def test_agent_external_endpoint_requires_user_approval(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from web.server import build_app

    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    api.attach_external_evidence(_external(store))
    client = TestClient(TestServer(build_app(api=api, upload_dir=tmp_path, token="t")))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer t"}
        denied = await client.post("/api/external/evidence", headers=headers,
                                   json={"question": "q", "queries": ["alpha"]})
        assert denied.status == 403
        assert (await denied.json())["status"] == "approval_required"
        ok = await client.post("/api/external/evidence", headers=headers,
                               json={"question": "q", "queries": ["alpha"], "user_approved": True})
        assert ok.status == 200 and (await ok.json())["evidence"]
    finally:
        await client.close()


async def test_external_paper_already_in_library_is_not_used_as_external() -> None:
    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    doc = await store.get_document("d1")
    assert doc is not None
    doc.local_meta = {"doi": "https://doi.org/10.1234/DRIFT"}
    await store.update_document(doc)
    api.attach_external_evidence(_external(store))

    result = await api.ask(question="alpha", collection="c1", external=True)

    assert [s["n"] for s in result["sources"]] == [1]
    assert result["external"]["in_library"] == 1 and result["external"]["used"] == 0


# ── 来源栏：详情、归类、导出 ──────────────────────────────────


async def test_capture_detail_placement_and_export(tmp_path: Path) -> None:
    from kacore.pipelines.external_prompts import Placement

    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    manager = _external(store)
    calls: list[int] = []

    async def fake_place(papers, outline):
        calls.append(len(papers))
        assert outline and outline[0][0]                     # 集合树带路径
        return [Placement(p.source_id, outline[0][0], "", "fits") for p in papers]

    manager.place = fake_place  # type: ignore[method-assign]
    api.attach_external_evidence(manager)
    from kacore.domain.models import Collection

    await store.upsert_collection(Collection(name="[26.3] Agentic", coll_key="Lagentic"))
    await store.set_document_collections("d1", ["Lagentic"])
    result = await api.ask(question="alpha", collection="c1", external=True)
    cid = result["external"]["capture_id"]

    detail = await api.get_external_capture(cid)
    assert detail["capture"]["ordinal"] == 1 and detail["capture"]["question"] == "alpha"
    [src] = detail["sources"]
    assert src["source_id"] == "doi:10.1234/drift" and src["trust"] == "verified"
    assert src["cited"][0]["passage"].startswith("Drift grows")
    assert {h["engine"] for h in src["hits"]} == {"crossref"}
    assert any(f["field"] == "title" for f in src["fields"])

    placed = await api.place_external_capture(cid)
    assert placed["sources"][0]["placement"]["reason"] == "fits"
    await api.place_external_capture(cid)
    assert calls == [1]                                      # 已归类的不再调用模型

    out = await api.export_external_capture(cid, str(tmp_path))
    csv_text = Path(out["files"][0]).read_text(encoding="utf-8")
    assert csv_text.splitlines()[0].startswith('"rank","capture_id","capture_timestamp"')
    assert '"cited"' in csv_text and "knowledge-arch" in csv_text
