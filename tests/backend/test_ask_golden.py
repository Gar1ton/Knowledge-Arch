"""问答链路的基准测试（P34 P0）：重构前用旧代码录下每种模式的完整外部行为，重构后逐项对照。

录的是调用方能观察到的一切：发给模型的提示词（按调用顺序）、返回结果、存进去的聊天记录、
运行事件与进度。时间戳与随机编号先规整成占位符再比较。

更新基准（只有行为本来就该变时才做，并在提交说明里写清为什么）：
    KA_UPDATE_GOLDEN=1 python -m pytest tests/backend/test_ask_golden.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from static_retriever import StaticRetriever

from kacore.adapters.tools.base import Tool, ToolEnv
from kacore.adapters.tools.registry import ToolBox
from kacore.api import KnowledgeRepositoryApi
from kacore.ask_progress import ProgressStore
from kacore.config import ToolsConfig
from kacore.domain.deep_thinking import DeepThinkingOutcome
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.domain.tool_calls import Block, ToolCallRequest, ToolResult, ToolSpec, ToolTurn
from kacore.index_compatibility import IndexCompatibilityStore
from kacore.managers.tool_distributor import ToolDistributor
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

GOLDEN = Path(__file__).parent / "golden" / "ask_modes.json"
UPDATE = os.environ.get("KA_UPDATE_GOLDEN") == "1"

# ── 替身 ────────────────────────────────────────────────────


class _LLM:
    """记下每次调用（方法、系统提示、用户提示），按用途回固定文本。"""

    configured = True

    def __init__(self, *, fail_generate_result: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail_generate_result

    async def generate_result(self, prompt: str, system_prompt: str = "", **kw: Any):
        self.calls.append({"m": "generate_result", "system": system_prompt, "user": prompt,
                           "allow_mock": str(kw.get("allow_mock"))})
        if self.fail:
            raise RuntimeError("model unavailable")
        return GenerationResult(text="Situations shape traits [1]. Drift grows [2].")

    async def generate(self, prompt: str, system_prompt: str = "", **kw: Any) -> str:
        self.calls.append({"m": "generate", "system": system_prompt, "user": prompt})
        if "translation" in system_prompt.lower():
            return "situations"
        if "standalone" in (system_prompt + prompt).lower() or "follow-up" in prompt.lower():
            return "situations shape traits"
        return "Graph answer about situations."


class _Retriever(StaticRetriever):
    async def retrieve_lightrag_context(self, collection, query, scope=None) -> str:
        return "graph context: situations relate to traits"


class _Deep:
    """deep_thinking / enhanced 编排器替身：with_answer=True 时像开了 verification 的路径。"""

    def __init__(self, chunk: DocumentChunk, actual_mode: str, with_answer: bool) -> None:
        self.chunk, self.actual_mode, self.with_answer = chunk, actual_mode, with_answer

    async def run(self, collection, question, scope, **_kw) -> DeepThinkingOutcome:
        return DeepThinkingOutcome(
            evidence=[self.chunk], actual_mode=self.actual_mode,
            answer="Deep answer [1]." if self.with_answer else None,
            answer_generation_status="", verified=self.with_answer)


class _Graph:
    def has_workspace(self, collection: str) -> bool:
        return True


class _Events:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add_event(self, **kw: Any) -> None:
        self.events.append(kw)


class _Progress(ProgressStore):
    def __init__(self) -> None:
        super().__init__()
        self.stages: list[list[Any]] = []

    def set(self, cid, stage, percent, detail=None):  # type: ignore[override]
        self.stages.append([stage, percent])
        return super().set(cid, stage, percent, detail)


RESULT = ("Title: Persona Drift\nAuthors: Gao, Li\nYear: 2025\nDOI: 10.1234/drift\n"
          "Abstract: Drift grows over long dialogue.")
CITE = {"call": "c1", "title": "Persona Drift", "creators": ["Gao, Li"], "year": "2025",
        "identifier": "10.1234/drift", "passage": "Drift grows over long dialogue."}


class _Search(Tool):
    spec = ToolSpec("fake_search", "Search.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block(RESULT)])


class _Model:
    def __init__(self) -> None:
        self.script = [[("fake_search", {"query": "drift"})], [("cite", CITE)], []]

    async def generate_with_tools(self, turns, tools, system_prompt=""):
        step = self.script.pop(0) if self.script else []
        return ToolTurn(GenerationResult(""), tuple(
            ToolCallRequest(f"t{i}", n, a) for i, (n, a) in enumerate(step)))


# ── 场景 ────────────────────────────────────────────────────

SCENARIOS: dict[str, dict[str, Any]] = {
    "default": {"kwargs": {"question": "situations", "collection": "c1"}},
    "default_global": {"kwargs": {"question": "situations"}},
    "default_no_hits": {"kwargs": {"question": "zebras", "collection": "c1"}},
    "default_llm_fails": {"kwargs": {"question": "situations", "collection": "c1"}, "fail": True},
    "default_no_llm": {"kwargs": {"question": "situations", "collection": "c1"}, "no_llm": True},
    "default_single_doc": {"kwargs": {"question": "situations", "doc_id": "d1"}},
    "default_translate": {"kwargs": {"question": "情境如何影响特质", "collection": "c1",
                                     "use_english_retrieval": True}},
    "default_context": {"kwargs": {"question": "and why?", "collection": "c1",
                                   "use_context": True}, "history": True},
    "default_lang_zh": {"kwargs": {"question": "situations", "collection": "c1",
                                   "answer_language": "zh"}},
    "enhanced": {"kwargs": {"question": "situations", "collection": "c1",
                            "retrieval_mode": "enhanced"}},
    "deep_with_answer": {"kwargs": {"question": "situations", "collection": "c1",
                                    "retrieval_mode": "deep_thinking"}, "deep_answer": True},
    "deep_fallback_synth": {"kwargs": {"question": "situations", "collection": "c1",
                                       "retrieval_mode": "deep_thinking"}},
    "graph_mixed": {"kwargs": {"question": "situations", "collection": "c1",
                               "retrieval_mode": "graph_mixed"}},
    "graph_only": {"kwargs": {"question": "situations", "collection": "c1",
                              "retrieval_mode": "graph_only"}},
    "graph_only_no_llm": {"kwargs": {"question": "situations", "collection": "c1",
                                     "retrieval_mode": "graph_only"}, "no_llm": True},
    "fulltext": {"kwargs": {"question": "situations", "retrieval_mode": "fulltext",
                            "doc_id": "d1"}},
    "fulltext_no_llm": {"kwargs": {"question": "situations", "retrieval_mode": "fulltext",
                                   "doc_id": "d1"}, "no_llm": True},
}
# 每个场景再跑一遍「联网补充开着」（装了工具），关着的那遍不装工具（与 2.2.0 一致的那条路）。
EXTERNAL_VARIANTS = ("off", "on")


async def _build(tmp_path: Path, spec: dict[str, Any], external: bool):
    store = InMemorySourceDocumentStore()
    bundle = tmp_path / "d1"
    bundle.mkdir(parents=True)
    (bundle / "clean.md").write_text("Situations shape traits. Drift grows.", encoding="utf-8")
    d1 = SourceDocument("d1", "Mischel and Shoda (1995) - Situations.pdf",
                        str(bundle / "original.pdf"), "application/pdf", 1, "h1", "c1")
    d1.markdown_rel_path = "clean.md"
    d1.local_meta = {"creators": ["Mischel, Walter"], "year": "1995"}
    await store.add_document(d1)
    await store.add_document(SourceDocument("d2", "Fleeson (2001) - Traits.pdf", "/d2.pdf",
                                            "application/pdf", 1, "h2", "c1"))
    await store.set_lightrag_index_status("d1", "c1", "indexed")
    chunks = [DocumentChunk("k1", "d1", 0, "situations shape traits", "h1",
                            metadata={"page_number": 3}),
              DocumentChunk("k2", "d2", 0, "traits vary across situations", "h2")]
    if spec.get("history"):
        await store.add_chat_message("conv", "user", "What are traits?")
        await store.add_chat_message("conv", "assistant", "Traits are dispositions.")
    compat = IndexCompatibilityStore(tmp_path / "compat.json")
    compat.mark_lightrag_compatible("c1", "fp")
    llm = None if spec.get("no_llm") else _LLM(fail_generate_result=bool(spec.get("fail")))
    events, progress = _Events(), _Progress()
    mode = spec["kwargs"].get("retrieval_mode")
    deep = _Deep(chunks[0], "milvus_deep" if mode == "deep_thinking" else "enhanced",
                 bool(spec.get("deep_answer")))
    api = KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=_Retriever({"c1": chunks, "": chunks}),  # type: ignore[arg-type]
        llm_adapter=llm,  # type: ignore[arg-type]
        lightrag_registry=_Graph(),  # type: ignore[arg-type]
        index_compatibility=compat, embedding_fingerprint="fp",
        deep_thinking_orchestrator=deep,  # type: ignore[arg-type]
        enhanced_recall_orchestrator=deep,  # type: ignore[arg-type]
        runtime_event_sink=events, progress_store=progress,
    )
    if external:
        cfg = ToolsConfig()
        box = ToolBox(config_provider=lambda: cfg, read_secret=lambda _r: "",
                      env=ToolEnv(cache_dir=Path("/nonexistent")), tools=(_Search,))
        api.attach_tool_distributor(ToolDistributor(toolbox=box, store=store, client=_Model(),
                                                    config_provider=lambda: cfg))
    return api, store, llm, events, progress


_VOLATILE_KEYS = {"created_at", "updated_at", "captured_at", "started_at", "timestamp", "ts",
                  "time", "elapsed_ms", "elapsed", "duration_ms", "id"}
# 随机编号：uuid 片段，以及账本运行编号 c_<12 位 hex>（前缀紧贴，\b 认不出）。
_HEX = re.compile(r"(?<![0-9a-z])(?:c_)?[0-9a-f]{12,}(?![0-9a-z])")


def _scrub(value: Any, tmp: str) -> Any:
    if isinstance(value, dict):
        return {k: ("<V>" if k in _VOLATILE_KEYS else _scrub(v, tmp)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, tmp) for v in value]
    if isinstance(value, str):
        return _HEX.sub("<HEX>", value.replace(tmp, "<TMP>"))
    if isinstance(value, float):
        return "<F>"
    return value


async def _record(name: str, external: str, tmp_path: Path) -> dict[str, Any]:
    spec = SCENARIOS[name]
    api, store, llm, events, progress = await _build(tmp_path, spec, external == "on")
    kwargs = {"conversation_id": "conv", **spec["kwargs"],
              **({"external": True} if external == "on" else {})}
    try:
        result: Any = await api.ask(**kwargs)
    except Exception as exc:  # noqa: BLE001 - 异常类型与信息也是行为的一部分
        result = {"raised": type(exc).__name__, "message": str(exc)}
    out = {
        "result": result,
        "llm_calls": llm.calls if llm is not None else [],
        "chat": await store.get_chat_messages("conv"),
        "events": [{k: e.get(k) for k in ("category", "operation", "status", "msg", "level",
                                          "metadata")} for e in events.events],
        "progress": progress.stages,
        "tool_calls": len(await store.list_recent_tool_calls(50)),
    }
    return _scrub(json.loads(json.dumps(out, default=str)), str(tmp_path))


CASES = [(n, e) for n in SCENARIOS for e in EXTERNAL_VARIANTS]


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    yield data
    if UPDATE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")


@pytest.mark.parametrize(("name", "external"), CASES, ids=[f"{n}-{e}" for n, e in CASES])
async def test_ask_matches_the_recorded_behaviour(name: str, external: str, tmp_path: Path,
                                                  golden: dict[str, Any]) -> None:
    key = f"{name}/{external}"
    got = await _record(name, external, tmp_path)
    if UPDATE:
        golden[key] = got
        return
    assert key in golden, f"no baseline for {key}; run with KA_UPDATE_GOLDEN=1"
    want = golden[key]
    for part in ("result", "llm_calls", "chat", "events", "progress", "tool_calls"):
        assert got[part] == want[part], f"{key}: {part} differs"


# ── 模式能力表（P34 P1 / P5）──────────────────────────────────


async def test_capabilities_publish_the_mode_table_the_ask_flow_enforces() -> None:
    """前端读的 ask_modes 就是问答入口执行的那张表：不另写一份规则。"""
    from kacore.config import Config
    from kacore.retrieval_modes import EXTERNAL_SKIPPED_MODES, MODES

    api = KnowledgeRepositoryApi(source_store=InMemorySourceDocumentStore(), config=Config({}))
    caps = await api.get_capabilities()
    published = {m["id"]: m for m in caps["ask_modes"]}
    assert list(published) == list(MODES)
    assert {m for m, s in published.items() if not s["supports_external"]} == EXTERNAL_SKIPPED_MODES
    assert published["fulltext"]["needs_doc"] and not published["graph_only"]["single_doc"]
    assert published["graph_mixed"]["needs_collection"] and published["graph_mixed"]["uses_graph"]


@pytest.mark.parametrize(("mode", "kwargs", "message"), [
    ("graph_only", {"doc_id": "d1", "collection": "c1"}, "cannot be limited to one document"),
    ("graph_mixed", {}, "requires a collection"),
    ("fulltext", {}, "requires a doc_id"),
    ("nope", {}, "retrieval_mode must be 'default', 'enhanced'"),
])
async def test_the_entry_checks_follow_the_mode_table(mode: str, kwargs: dict[str, Any],
                                                      message: str, tmp_path: Path) -> None:
    api, *_ = await _build(tmp_path, {"kwargs": {}}, external=False)
    with pytest.raises(ValueError, match=message):
        await api.ask(question="q", retrieval_mode=mode, **kwargs)
