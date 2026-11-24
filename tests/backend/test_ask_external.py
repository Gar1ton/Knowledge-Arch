"""ask(external=True) 与工具接口的端到端契约：外部引用续接编号、Harvard 引用、账本标记 cited、
来源栏详情 / 归类 / 导出，以及 skill 路径（助手自己调用工具，KA 只执行、核对、记账）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from static_retriever import StaticRetriever

from kacore.adapters.tools.base import Tool, ToolEnv
from kacore.adapters.tools.registry import ToolBox
from kacore.api import KnowledgeRepositoryApi
from kacore.config import ToolsConfig
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.domain.tool_calls import Block, ToolCallRequest, ToolResult, ToolSpec, ToolTurn
from kacore.managers.tool_distributor import ToolDistributor
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

RESULT = ("Title: Persona Drift\nAuthors: Gao, Li\nYear: 2025\nDOI: 10.1234/drift\n"
          "Abstract: Drift grows over long dialogue.")
CITE = {"call": "c1", "title": "Persona Drift", "creators": ["Gao, Li"], "year": "2025",
        "identifier": "10.1234/drift", "passage": "Drift grows over long dialogue."}


class _Search(Tool):
    spec = ToolSpec("fake_search", "Search.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block(RESULT)])


class _Model:
    """research 模型：第一轮检索，第二轮登记引用，第三轮停。"""

    def __init__(self) -> None:
        self.script = [[("fake_search", {"query": "drift"})], [("cite", CITE)], []]

    async def generate_with_tools(self, turns, tools, system_prompt=""):
        step = self.script.pop(0) if self.script else []
        return ToolTurn(GenerationResult(""), tuple(
            ToolCallRequest(f"t{i}", n, a) for i, (n, a) in enumerate(step)))


class _SynthLLM:
    """合成回答：引用库内 [1] 与外部 [2]；记录收到的提示词。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_result(self, prompt: str, system_prompt: str = "", **_kw):
        self.prompts.append(prompt)
        return GenerationResult(text="Situations constrain traits [1]. Drift grows [2].")


def _distributor(store: InMemorySourceDocumentStore, llm: Any = None) -> ToolDistributor:
    cfg = ToolsConfig()
    box = ToolBox(config_provider=lambda: cfg, read_secret=lambda _r: "",
                  env=ToolEnv(cache_dir=Path("/nonexistent")), tools=(_Search,))
    return ToolDistributor(toolbox=box, store=store, client=_Model(),
                           config_provider=lambda: cfg, llm=llm)


async def _api(store: InMemorySourceDocumentStore, llm: _SynthLLM) -> KnowledgeRepositoryApi:
    await store.add_document(SourceDocument(
        doc_id="d1", title="Mischel and Shoda (1995) - A Cognitive-Affective System Theory.pdf",
        file_path="/d1.pdf", content_type="application/pdf", size_bytes=1, content_hash="h",
        collection="c1"))
    retriever = StaticRetriever({"c1": [DocumentChunk("k1", "d1", 0, "alpha situations", "h")]})
    return KnowledgeRepositoryApi(source_store=store, retrieval_orchestrator=retriever,
                                  llm_adapter=llm)  # type: ignore[arg-type]


async def test_external_citation_is_used_in_the_answer_and_marked_cited() -> None:
    store = InMemorySourceDocumentStore()
    llm = _SynthLLM()
    api = await _api(store, llm)
    api.attach_tool_distributor(_distributor(store))

    result = await api.ask(question="alpha", collection="c1", external=True)

    assert "[2] Persona Drift [external]" in llm.prompts[-1]
    local, ext = result["sources"]
    assert local["n"] == 1 and local["origin"] != "external"
    assert ext["n"] == 2 and ext["origin"] == "external" and ext["tool"] == "fake_search"
    assert "(Gao, 2025)" in result["answer"]
    summary = result["external"]
    assert (summary["status"], summary["used"], summary["cited"]) == ("ok", 1, 1)
    messages = await store.get_chat_messages(result["conversation_id"])
    assert messages[-1]["meta"]["external"]["capture_id"] == summary["capture_id"]
    capture = await store.get_capture(summary["capture_id"])
    assert capture.msg_idx == len(messages) - 1
    [citation] = await store.list_citations(summary["capture_id"])
    assert citation.cited


async def test_without_tools_the_answer_still_comes_from_the_library() -> None:
    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    result = await api.ask(question="alpha", collection="c1", external=True)
    assert result["external"]["status"] == "unavailable"
    assert [s["n"] for s in result["sources"]] == [1] and result["answer"]


async def test_with_external_off_the_answer_is_exactly_as_without_the_feature() -> None:
    """用户 11-23：关掉联网时正常功能完全不变。装了 distributor 但没打开开关，与根本没装时
    提示词、返回结果、聊天记录逐项相同，也不发任何工具调用、不写账本。"""
    results, prompts, metas = [], [], []
    for attach in (False, True):
        store = InMemorySourceDocumentStore()
        llm = _SynthLLM()
        api = await _api(store, llm)
        if attach:
            api.attach_tool_distributor(_distributor(store))
        result = await api.ask(question="alpha", collection="c1", conversation_id="conv")
        assert "external" not in result
        assert await store.list_recent_tool_calls(5) == []
        messages = await store.get_chat_messages("conv")
        assert "external" not in (messages[-1].get("meta") or {})
        results.append(result)
        prompts.append(llm.prompts)
        metas.append(messages[-1].get("meta"))
    assert prompts[0] == prompts[1] and metas[0] == metas[1]
    assert results[0] == results[1]


async def test_a_citation_already_in_the_library_is_not_used_as_external() -> None:
    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    doc = await store.get_document("d1")
    assert doc is not None
    doc.local_meta = {"doi": "https://doi.org/10.1234/DRIFT"}
    await store.update_document(doc)
    api.attach_tool_distributor(_distributor(store))

    result = await api.ask(question="alpha", collection="c1", external=True)

    assert [s["n"] for s in result["sources"]] == [1]
    assert result["external"]["in_library"] == 1 and result["external"]["used"] == 0


BOOK = ("Title: Parables for the Virtual\nAuthors: Massumi, Brian\nYear: 2002\n"
        "ISBN: 9780822328971; 0822328976\nAbstract: Affect is autonomous.")
BOOK_CITE = {"call": "c1", "title": "Parables for the Virtual", "creators": ["Massumi, Brian"],
             "year": "2002", "identifier": "9780822328971", "passage": "Affect is autonomous."}


class _BookSearch(Tool):
    spec = ToolSpec("fake_search", "Search.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block(BOOK)])


class _BookModel(_Model):
    def __init__(self) -> None:
        self.script = [[("fake_search", {"query": "affect"})], [("cite", BOOK_CITE)], []]


async def test_a_book_without_doi_already_in_zotero_is_matched_by_isbn() -> None:
    """用户 11-24：没有 DOI 的书按 ISBN 认出库里已有（Zotero 写 10 位，引用写 13 位）。"""
    from kacore.domain.models import ZoteroItem

    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    doc = await store.get_document("d1")
    assert doc is not None
    doc.library_id, doc.zotero_item_key = "9", "BOOK1"
    await store.update_document(doc)
    await store.upsert_zotero_item(ZoteroItem("BOOK1", "9", "book",
                                              raw_zotero_json={"ISBN": "0-8223-2897-6"}))
    cfg = ToolsConfig()
    box = ToolBox(config_provider=lambda: cfg, read_secret=lambda _r: "",
                  env=ToolEnv(cache_dir=Path("/nonexistent")), tools=(_BookSearch,))
    api.attach_tool_distributor(ToolDistributor(toolbox=box, store=store, client=_BookModel(),
                                                config_provider=lambda: cfg))

    result = await api.ask(question="alpha", collection="c1", external=True)

    assert result["external"]["registered"] == 1
    assert [s["n"] for s in result["sources"]] == [1]
    assert result["external"]["in_library"] == 1 and result["external"]["used"] == 0


class _GraphOnly:
    """只用图谱：没有来源编号，外部引用无处续接。"""

    async def retrieve_lightrag_context(self, collection, query, scope=None) -> str:
        return "graph context"


class _Graph:
    def has_workspace(self, collection: str) -> bool:
        return True


class _PlainLLM(_SynthLLM):
    """只用图谱走 generate()（不带引用）。"""

    async def generate(self, prompt: str, system_prompt: str = "", **_kw) -> str:
        return "graph answer"


async def _skipped_mode_api(mode: str, tmp_path: Path) -> tuple[KnowledgeRepositoryApi, Any]:
    from kacore.index_compatibility import IndexCompatibilityStore

    store = InMemorySourceDocumentStore()
    bundle = tmp_path / "d1"
    bundle.mkdir(parents=True)
    (bundle / "clean.md").write_text("short body", encoding="utf-8")
    doc = SourceDocument("d1", "d1", str(bundle / "original.pdf"), "application/pdf", 1, "h", "c1")
    doc.markdown_rel_path = "clean.md"
    await store.add_document(doc)
    await store.set_lightrag_index_status("d1", "c1", "indexed")
    compatibility = IndexCompatibilityStore(tmp_path / "compat.json")
    compatibility.mark_lightrag_compatible("c1", "fp")
    retriever = _GraphOnly() if mode == "graph_only" else StaticRetriever({"c1": []})
    api = KnowledgeRepositoryApi(source_store=store, retrieval_orchestrator=retriever,  # type: ignore[arg-type]
                                 llm_adapter=_PlainLLM(),  # type: ignore[arg-type]
                                 lightrag_registry=_Graph(),  # type: ignore[arg-type]
                                 index_compatibility=compatibility, embedding_fingerprint="fp")
    api.attach_tool_distributor(_distributor(store))
    return api, store


async def test_fulltext_and_graph_only_say_external_sources_did_not_run(tmp_path: Path) -> None:
    """用户 11-24：这两种模式不联网，开关开着时要如实说明，而不是悄悄忽略。"""
    for mode in ("fulltext", "graph_only"):
        api, store = await _skipped_mode_api(mode, tmp_path / mode)
        kwargs = {"doc_id": "d1"} if mode == "fulltext" else {"collection": "c1"}
        result = await api.ask(question="q", retrieval_mode=mode, external=True,
                               conversation_id="on", **kwargs)
        assert result["external"] == {"status": "unavailable", "reason": "mode"}, mode
        messages = await store.get_chat_messages("on")
        assert messages[-1]["meta"]["external"] == {"status": "unavailable", "reason": "mode"}
        assert await store.list_recent_tool_calls(5) == []

        off = await api.ask(question="q", retrieval_mode=mode, conversation_id="off", **kwargs)
        assert "external" not in off, mode
        assert "external" not in ((await store.get_chat_messages("off"))[-1].get("meta") or {})


# ── 来源栏：详情、归类、导出 ──────────────────────────────────


async def test_capture_detail_placement_and_export(tmp_path: Path) -> None:
    from kacore.domain.models import Collection
    from kacore.pipelines.external_prompts import Placement

    store = InMemorySourceDocumentStore()
    api = await _api(store, _SynthLLM())
    distributor = _distributor(store)
    placed_calls: list[int] = []

    async def fake_place(papers, outline):
        placed_calls.append(len(papers))
        return [Placement(p.source_id, outline[0][0], "", "fits") for p in papers]

    distributor.place = fake_place  # type: ignore[method-assign]
    api.attach_tool_distributor(distributor)
    await store.upsert_collection(Collection(name="[26.3] Agentic", coll_key="Lagentic"))
    await store.set_document_collections("d1", ["Lagentic"])
    result = await api.ask(question="alpha", collection="c1", external=True)
    cid = result["external"]["capture_id"]

    detail = await api.get_external_capture(cid)
    [src] = detail["sources"]
    assert (src["source_id"], src["tool"], src["call_label"], src["cited"]) == (
        "E1", "fake_search", "c1", True)
    assert [c["tool"] for c in detail["calls"]] == ["fake_search", "cite"]
    assert detail["calls"][0]["blocks"][0]["text"] == RESULT          # 原文可回看

    assert (await api.place_external_capture(cid))["sources"][0]["placement"]["reason"] == "fits"
    await api.place_external_capture(cid)
    assert placed_calls == [1]                                       # 已归类的不再调用模型

    out = await api.export_external_capture(cid, str(tmp_path))
    header = Path(out["files"][0]).read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith('"rank","capture_id","capture_timestamp"')
    assert header.endswith('"tool","call_id","block","locator","passage","cited"')


# ── skill：助手自己调用工具 ─────────────────────────────────


async def test_agent_calls_tools_and_cites_without_ka_model(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from web.server import build_app

    store = InMemorySourceDocumentStore()
    llm = _SynthLLM()
    api = await _api(store, llm)
    api.attach_tool_distributor(_distributor(store))
    client = TestClient(TestServer(build_app(api=api, upload_dir=tmp_path, token="t")))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer t"}
        denied = await client.post("/api/tools/fake_search/call", headers=headers,
                                   json={"arguments": {"query": "drift"}})
        assert denied.status == 403
        called = await client.post("/api/tools/fake_search/call", headers=headers, json={
            "arguments": {"query": "drift"}, "question": "q", "user_approved": True})
        capture_id = (await called.json())["capture_id"]
        cited = await (await client.post("/api/tools/cite", headers=headers,
                                         json={"capture_id": capture_id, **CITE})).json()
        assert cited["accepted"] and cited["source"]["harvard_in_text"] == "Gao, 2025"
        tools = await (await client.get("/api/tools", headers=headers)).json()
        assert tools["tools"][0]["name"] == "fake_search"
    finally:
        await client.close()
    assert llm.prompts == []                                         # KA 不调用模型
    assert (await store.get_capture(capture_id)).actor == "agent"
