"""提示词按回答语言切换：kacore/pipelines/prompt_language.py 与各提示词模块的双语一致性。"""
from __future__ import annotations

import re

import pytest
from static_retriever import StaticRetriever

from kacore.api import KnowledgeRepositoryApi
from kacore.domain.deep_thinking import Checklist, ChecklistItem, EvidenceItem
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.pipelines import answer_synthesis as an
from kacore.pipelines import deep_thinking_prompts as dp
from kacore.pipelines import enhanced_recall_prompts as ep
from kacore.pipelines.conversation_context import build_condense_prompt
from kacore.pipelines.evidence_actions import ACTION_GUIDANCE
from kacore.pipelines.llm_json import JSON_RETRY_SUFFIX
from kacore.pipelines.prompt_language import prompt_lang, uses_chinese
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

CHUNK = DocumentChunk("c1", "d1", 0, "evidence text", "h")
LABELS = {"d1": "Smith 2020"}


def test_prompt_lang_follows_answer_language_then_question() -> None:
    assert prompt_lang("zh", "What is X?") == "zh"
    assert prompt_lang("en", "什么是 X？") == "en"
    assert prompt_lang("auto", "什么是 X？") == "zh"
    assert prompt_lang("auto", "What is X?") == "en"
    assert prompt_lang("unexpected", "Qu'est-ce que X ?") == "en"


def _prompts(lang: str) -> dict[str, str]:
    checklist = Checklist(items=[ChecklistItem(id="c1", text="point", critical=True)])
    evidence = [EvidenceItem(chunk=CHUNK, kept_reason="rerank_score")]
    return {
        "plan_system": dp.PLAN_SYSTEM[lang],
        "plan": dp.build_plan_prompt("Q", 3, lang=lang),
        "sea_system": dp.SEA_SYSTEM[lang],
        "sea": dp.build_sea_prompt("Q", checklist, evidence, 320, LABELS, 4, lang=lang),
        "verify_system": dp.VERIFY_SYSTEM[lang],
        "verify": dp.build_verify_prompt("Q", "A [1]", [CHUNK], 320, LABELS, lang=lang),
        "plan_lite_system": ep.PLAN_LITE_SYSTEM[lang],
        "plan_lite": ep.build_plan_lite_prompt("Q", 3, lang=lang),
        "synth_check_system": ep.build_synth_check_system("auto", 2, lang=lang),
        "synth_check": ep.build_synth_check_prompt("Q", [CHUNK], LABELS, lang=lang),
        "synth_check_retry": ep.SYNTH_CHECK_RETRY_SUFFIX[lang],
        "base_system": an._SYNTH_SYSTEM_BASE[lang],
        "enhanced_system": an._SYNTH_SYSTEM_ENHANCED[lang],
        "deep_system": an._SYNTH_SYSTEM_DEEP[lang],
        "fulltext_system": an._SYNTH_SYSTEM_FULLTEXT[lang],
        "rag_rules": an.rag_rules(lang, "auto", cite=True),
        "condense": "\n".join(build_condense_prompt([("q1", "a1")], "Q", lang=lang)),
        "actions": ACTION_GUIDANCE[lang],
        "json_retry": JSON_RETRY_SUFFIX[lang],
    }


ZH = _prompts("zh")
EN = _prompts("en")


@pytest.mark.parametrize("name", sorted(ZH))
def test_each_prompt_is_written_in_its_language(name: str) -> None:
    assert uses_chinese(ZH[name]), name
    assert not uses_chinese(EN[name]), name


def _json_keys(text: str) -> set[str]:
    return set(re.findall(r'"([a-z_]+)":', text))


def _enums(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]+(?:\|[a-z_]+)+", text))


@pytest.mark.parametrize("name", sorted(ZH))
def test_json_contract_is_identical_in_both_languages(name: str) -> None:
    """解析器依赖的字段名、枚举值、编号与标记不随语言变化。"""
    assert _json_keys(ZH[name]) == _json_keys(EN[name]), name
    assert _enums(ZH[name]) == _enums(EN[name]), name
    assert set(re.findall(r"\[\d+\]|c\d|===VERDICT===", ZH[name])) == set(
        re.findall(r"\[\d+\]|c\d|===VERDICT===", EN[name])
    ), name


def test_source_tag_follows_prompt_language() -> None:
    assert an.source_tag("d1", LABELS, "zh") == "（来源：Smith 2020）"
    assert an.source_tag("d1", LABELS, "en") == " (Source: Smith 2020)"
    assert an.source_tag("d1", None, "en") == ""


class _CapturingLLM:
    def __init__(self) -> None:
        self.systems: list[str] = []

    async def generate_result(self, prompt, system_prompt="", *, allow_mock=True):
        from kacore.domain.llm_generation import GenerationResult

        self.systems.append(system_prompt)
        return GenerationResult(text="answer [1]")

    async def generate(self, prompt, system_prompt="", *, allow_mock=True):
        self.systems.append(system_prompt)
        return "answer [1]"


@pytest.mark.parametrize(("answer_language", "question", "chinese"), [
    ("en", "这篇论文的方法是什么？", False),
    ("auto", "这篇论文的方法是什么？", True),
    ("auto", "What method does the paper use?", False),
])
async def test_default_mode_prompt_language(
    answer_language: str, question: str, chinese: bool
) -> None:
    store = InMemorySourceDocumentStore()
    await store.add_document(SourceDocument(
        doc_id="d1", title="d1", file_path="/data/d1.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h", collection="papers",
    ))
    llm = _CapturingLLM()
    api = KnowledgeRepositoryApi(
        source_store=store,
        # StaticRetriever 按子串命中：分块正文含整句问题才会被召回。
        retrieval_orchestrator=StaticRetriever(
            {"papers": [DocumentChunk("c1", "d1", 0, question, "h")]}
        ),
        llm_adapter=llm,  # type: ignore[arg-type]
    )

    await api.ask(question=question, collection="papers", answer_language=answer_language)

    assert llm.systems, "the answer was not generated by the LLM"
    assert uses_chinese(llm.systems[-1]) is chinese
