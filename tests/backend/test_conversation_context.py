"""多轮对话：kacore/pipelines/conversation_context.py 与 api.ask(use_context=...)。"""
from __future__ import annotations

from static_retriever import StaticRetriever

from kacore.api import KnowledgeRepositoryApi
from kacore.domain.deep_thinking import DeepThinkingOutcome
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.pipelines.conversation_context import (
    MAX_ANSWER_CHARS,
    build_condense_prompt,
    clean_rewrite,
    recent_turns,
)
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

REWRITTEN = "What are the limitations of the Transformer?"


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ── 纯函数 ────────────────────────────────────────────────────────


def test_recent_turns_pairs_questions_with_answers_and_keeps_the_last_ones() -> None:
    messages = [
        _msg("user", "q1"), _msg("assistant", "a1"),
        _msg("user", "q2"), _msg("assistant", "a2"),
        _msg("user", "lost"),  # 超时后还没写入答案
        _msg("user", "q3"), _msg("assistant", "a3"),
        _msg("user", "q4"), _msg("assistant", "a4"),
    ]
    assert recent_turns(messages) == [("q2", "a2"), ("q3", "a3"), ("q4", "a4")]
    assert recent_turns(messages, max_turns=1) == [("q4", "a4")]
    assert recent_turns([]) == []


def test_condense_prompt_clips_long_answers() -> None:
    user, system = build_condense_prompt(
        [("What is a Transformer?", "x" * 5000)], "its limits?", lang="en"
    )
    assert "Follow-up question: its limits?" in user
    assert "x" * MAX_ANSWER_CHARS + " …" in user and "x" * (MAX_ANSWER_CHARS + 1) not in user
    assert "standalone question" in system


def test_clean_rewrite_strips_labels_and_quotes() -> None:
    assert clean_rewrite('Rewritten question: "What is X?"\n\nextra') == "What is X?"
    assert clean_rewrite("「它的局限是什么？」") == "它的局限是什么？"
    assert clean_rewrite("   ") == ""
    assert clean_rewrite("y" * 501) == ""


# ── api.ask ──────────────────────────────────────────────────────


class _RewriteLLM:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.rewrite_calls = 0

    async def generate(self, prompt, system_prompt="", *, allow_mock=True):
        if "rewrite follow-up questions" in system_prompt:
            self.rewrite_calls += 1
            if self.fail:
                raise RuntimeError("model offline")
            return f"Standalone question: {REWRITTEN}"
        return "unused"


class _RecordingDeep:
    def __init__(self) -> None:
        self.query = ""
        self.answer_question = None

    async def run(self, collection, query, scope=None, progress=None,
                  answer_language="auto", answer_question=None):
        self.query = query
        self.answer_question = answer_question
        return DeepThinkingOutcome(
            evidence=[DocumentChunk("c0", "d1", 0, "ev", "h0")],
            answer="verified answer [1]",
            verified=True,
            actual_mode="milvus_deep",
        )


async def _api(llm: _RewriteLLM, *, history: bool) -> tuple[KnowledgeRepositoryApi, _RecordingDeep,
                                                          InMemorySourceDocumentStore]:
    store = InMemorySourceDocumentStore()
    await store.add_document(SourceDocument(
        doc_id="d1", title="d1", file_path="/data/d1.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h", collection="papers",
    ))
    if history:
        await store.add_chat_message("conv", "user", "What is a Transformer?")
        await store.add_chat_message("conv", "assistant", "A Transformer is an attention model.")
    deep = _RecordingDeep()
    api = KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=StaticRetriever({"papers": []}),
        llm_adapter=llm,  # type: ignore[arg-type]
        deep_thinking_orchestrator=deep,  # type: ignore[arg-type]
    )
    return api, deep, store


async def _ask(api: KnowledgeRepositoryApi, *, use_context: bool) -> dict:
    return await api.ask(
        question="What are its limitations?",
        collection="papers",
        conversation_id="conv",
        retrieval_mode="deep_thinking",
        use_context=use_context,
    )


async def test_follow_up_is_rewritten_for_retrieval_but_stored_as_asked() -> None:
    llm = _RewriteLLM()
    api, deep, store = await _api(llm, history=True)

    result = await _ask(api, use_context=True)

    assert llm.rewrite_calls == 1
    assert deep.query == REWRITTEN
    assert result["standalone_question"] == REWRITTEN
    users = [m["content"] for m in await store.get_chat_messages("conv") if m["role"] == "user"]
    assert users[-1] == "What are its limitations?"


async def test_first_turn_and_disabled_context_do_not_rewrite() -> None:
    llm = _RewriteLLM()
    api, deep, _ = await _api(llm, history=False)
    result = await _ask(api, use_context=True)
    assert llm.rewrite_calls == 0 and "standalone_question" not in result
    assert deep.query == "What are its limitations?"

    api, deep, _ = await _api(llm, history=True)
    result = await _ask(api, use_context=False)
    assert llm.rewrite_calls == 0 and "standalone_question" not in result


async def test_rewrite_failure_falls_back_to_the_question_as_asked() -> None:
    llm = _RewriteLLM(fail=True)
    api, deep, _ = await _api(llm, history=True)

    result = await _ask(api, use_context=True)

    assert llm.rewrite_calls == 1
    assert deep.query == "What are its limitations?"
    assert "standalone_question" not in result


async def test_trace_and_rewrite_are_kept_in_chat_history() -> None:
    """超时补拉与重启后读历史时，思考过程与「理解为」从聊天记录的 meta 恢复。"""
    llm = _RewriteLLM()
    api, _, store = await _api(llm, history=True)

    await _ask(api, use_context=True)

    assistant = [m for m in await store.get_chat_messages("conv") if m["role"] == "assistant"][-1]
    assert assistant["meta"]["standalone_question"] == REWRITTEN
    assert assistant["meta"]["thinking_trace"]["verified"] is True
