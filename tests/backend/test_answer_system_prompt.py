"""`flow.answer.system_prompt` 契约：非空时放在 RAG 约束之前，为空时不加；deep / enhanced 不变。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from static_retriever import StaticRetriever

from kacore.api import KnowledgeRepositoryApi
from kacore.config import Config
from kacore.domain.deep_thinking import DeepThinkingOutcome
from kacore.domain.llm_generation import GenerationResult
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.pipelines.answer_synthesis import with_user_system_prompt
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

USER_PROMPT = "你是一位严谨的科研助理，回答时先给结论。"
RAG_PREFIX = "You are a helpful academic assistant."


class _RecordingLLM:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def generate_result(self, prompt: str, system_prompt: str = "", **_kw: Any):
        self.system_prompts.append(system_prompt)
        return GenerationResult(text="答案 [1]")


def _doc(doc_id: str = "d1", collection: str = "kb1") -> SourceDocument:
    return SourceDocument(doc_id, doc_id, f"/data/{doc_id}.pdf", "application/pdf", 1, "h",
                          collection)


async def _api(system_prompt: str | None, llm: _RecordingLLM, **kwargs: Any):
    store = InMemorySourceDocumentStore()
    await store.add_document(_doc())
    raw = {"ask": {"system_prompt": system_prompt}} if system_prompt is not None else {}
    return KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=StaticRetriever(
            {"kb1": [DocumentChunk("c0", "d1", 0, "relevant text", "h0")]}
        ),
        llm_adapter=llm,  # type: ignore[arg-type]
        config=Config(raw),
        **kwargs,
    )


def test_helper_prepends_and_keeps_empty_unchanged() -> None:
    assert with_user_system_prompt("", "RULES") == "RULES"
    assert with_user_system_prompt("   \n", "RULES") == "RULES"
    assert with_user_system_prompt(" 人设 ", "RULES") == "人设\n\nRULES"


async def test_default_ask_puts_user_prompt_before_rag_rules() -> None:
    llm = _RecordingLLM()
    api = await _api(USER_PROMPT, llm)
    await api.ask(question="relevant", collection="kb1")
    system = llm.system_prompts[-1]
    assert system.startswith(USER_PROMPT)
    assert system.index(USER_PROMPT) < system.index(RAG_PREFIX)
    assert "Cite sources using [n]" in system


async def test_empty_system_prompt_sends_plain_rag_rules() -> None:
    llm = _RecordingLLM()
    api = await _api("", llm)
    await api.ask(question="relevant", collection="kb1")
    assert llm.system_prompts[-1].startswith(RAG_PREFIX)


async def test_system_prompt_is_read_per_call() -> None:
    llm = _RecordingLLM()
    api = await _api("", llm)
    await api.ask(question="relevant", collection="kb1")
    api._config.raw.setdefault("ask", {})["system_prompt"] = USER_PROMPT  # type: ignore[union-attr]
    await api.ask(question="relevant", collection="kb1")
    assert llm.system_prompts[0].startswith(RAG_PREFIX)
    assert llm.system_prompts[1].startswith(USER_PROMPT)


async def test_fulltext_ask_uses_user_prompt(tmp_path: Path) -> None:
    llm = _RecordingLLM()
    store = InMemorySourceDocumentStore()
    doc = _doc("d1", "papers")
    bundle = tmp_path / "d1"
    bundle.mkdir()
    (bundle / "clean.md").write_text("short body", encoding="utf-8")
    doc.file_path = str(bundle / "original.pdf")
    doc.markdown_rel_path = "clean.md"
    await store.add_document(doc)
    api = KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=StaticRetriever({"papers": []}),
        llm_adapter=llm,  # type: ignore[arg-type]
        config=Config({"ask": {"system_prompt": USER_PROMPT}}),
    )
    await api.ask(question="q", retrieval_mode="fulltext", doc_id="d1")
    assert llm.system_prompts[-1].startswith(USER_PROMPT)


class _EnhancedWithoutAnswer:
    async def run(self, *_args: Any, **_kwargs: Any) -> DeepThinkingOutcome:
        return DeepThinkingOutcome(
            evidence=[DocumentChunk("c0", "d1", 0, "relevant text", "h0")],
            actual_mode="enhanced",
        )


async def test_enhanced_path_is_not_affected() -> None:
    llm = _RecordingLLM()
    api = await _api(USER_PROMPT, llm, enhanced_recall_orchestrator=_EnhancedWithoutAnswer())
    await api.ask(question="relevant", collection="kb1", retrieval_mode="enhanced")
    assert llm.system_prompts, "enhanced 兜底合成应调用 LLM"
    assert all(USER_PROMPT not in system for system in llm.system_prompts)
