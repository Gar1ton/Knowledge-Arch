"""增强召回（enhanced）两步 LLM 契约：PLAN-lite 与 SYNTH+CHECK（pipelines 层）。

为何独立成文件：与 deep_thinking_prompts 同理——把 prompt 与解析契约集中，使
orchestrator 只关心控制流。两步契约：
- PLAN-lite：纯 JSON（改写 + ≤N 个子查询），解析失败抛 JsonContractError 重试。
- SYNTH+CHECK：markdown 正文 + 尾部 ``===VERDICT===`` 标记行 + 充分性 JSON——
  verdict 走尾部标记而非把长答案整体塞进 JSON，规避转义脆弱；标记/JSON 缺失一律
  fail-open（整段视为答案、sufficient=False），好答案不因格式问题浪费重试调用；
  仅正文为空才抛 JsonContractError（由 llm_json_call 重试）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kacore.pipelines.answer_synthesis import (
    _SYNTH_SYSTEM_ENHANCED,
    context_prompt,
    lang_instruction,
    source_tag,
)
from kacore.pipelines.deep_thinking_prompts import JsonContractError, extract_json_object
from kacore.pipelines.evidence_actions import ACTION_GUIDANCE, parse_actions
from kacore.pipelines.evidence_views import evidence_text
from kacore.pipelines.prompt_language import PromptLang

if TYPE_CHECKING:
    from kacore.domain.models import DocumentChunk

# SYNTH+CHECK 的 verdict 分隔标记（正文之后另起一行）。
VERDICT_MARKER = "===VERDICT==="

# SYNTH+CHECK 正文为空时的重试提示（覆盖 llm_json_call 默认的纯 JSON 后缀）。
SYNTH_CHECK_RETRY_SUFFIX: dict[PromptLang, str] = {
    "zh": (
        "\n\n注意：回答正文不能为空。先输出 markdown 正文，"
        f"再另起一行输出 {VERDICT_MARKER} 与充分性 JSON 对象。"
    ),
    "en": (
        "\n\nNote: the answer body must not be empty. Output the markdown body first, then on "
        f"a new line output {VERDICT_MARKER} and the sufficiency JSON object."
    ),
}


# ── PLAN-lite：改写 + 拆解（单次调用）───────────────────────
PLAN_LITE_SYSTEM: dict[PromptLang, str] = {
    "zh": (
        "你是检索规划助手。把用户问题改写为一条自包含的检索问题，并拆出互补的检索子查询。"
        "优先识别关键实体、术语、方法名、数据集名、模型名和章节/图表锚点。"
        "只输出 JSON，不要任何额外文字。"
    ),
    "en": (
        "You are a retrieval-planning assistant. Rewrite the user's question as one "
        "self-contained retrieval question and split out complementary retrieval sub-queries. "
        "Prioritize identifying key entities, terms, method names, dataset names, model names "
        "and section/figure/table anchors. Output JSON only, with no extra text."
    ),
}


def build_plan_lite_prompt(query: str, max_sub_queries: int, *, lang: PromptLang) -> str:
    if lang == "en":
        return (
            f"User question: {query}\n\n"
            "Output JSON in this format:\n"
            '{"rewritten_query":"a self-contained retrieval question with ambiguities and '
            'references resolved",'
            '"sub_queries":["a retrieval query focused on one aspect, with keywords, entities or '
            'anchors"]}\n'
            f"Requirements: give 1-{max_sub_queries} sub_queries that each cover a different "
            "aspect (definition/background, mechanism, comparison, timeline, section anchors, "
            "verbatim evidence); "
            "if the question compares several objects, give a probe for each object separately "
            "plus a cross-object comparison probe; "
            "do not answer the question, only rewrite and split it."
        )
    return (
        f"用户问题：{query}\n\n"
        "请输出 JSON，格式：\n"
        '{"rewritten_query":"消歧、补全指代后的自包含检索问题",'
        '"sub_queries":["聚焦某一方面且含关键词/实体/锚点的检索query"]}\n'
        f"要求：sub_queries 给 1~{max_sub_queries} 个、彼此覆盖不同方面"
        "（定义/背景、机制、对比、时间线、章节锚点、原文证据）；"
        "若问题在比较多个对象，必须为每个对象分别给探针，再加跨对象对比探针；"
        "不要回答问题，只做改写与拆解。"
    )


def parse_plan_lite(raw: str) -> tuple[str, list[str]]:
    """解析 PLAN-lite 输出为 (rewritten_query, sub_queries)。

    宽容解析：sub_queries 元素可为字符串或含 query/text/q 键的对象；
    rewritten 为空由调用方回退原 query。两者全空 → JsonContractError（重试）。
    """
    obj = extract_json_object(raw)
    rewritten = str(obj.get("rewritten_query") or obj.get("rewritten") or "").strip()
    subs: list[str] = []
    for q in obj.get("sub_queries", []) or []:
        if isinstance(q, dict):
            text = str(q.get("query") or q.get("text") or q.get("q") or "").strip()
        else:
            text = str(q).strip()
        if text and text not in subs:
            subs.append(text)
    if not rewritten and not subs:
        raise JsonContractError("empty rewritten_query and sub_queries")
    return rewritten, subs


# ── SYNTH+CHECK：合成 + 内嵌充分性自检（单次调用）───────────
@dataclass
class SynthCheckResult:
    """SYNTH+CHECK 解析结果。fail-open 路径下 sufficient=False、reasons/queries 为空。"""

    answer: str = ""
    sufficient: bool = False
    insufficiency_reasons: list[str] = field(default_factory=list)
    corrective_queries: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)


_VERDICT_RULE: dict[PromptLang, str] = {
    "en": (
        f" After you finish the answer, output a line containing exactly {VERDICT_MARKER} "
        "followed by ONE JSON object on the lines after it: "
        '{"sufficient":true|false,"insufficiency_reasons":["what evidence is missing"],'
        '"corrective_queries":["directly searchable follow-up query"]}. '
        "Set sufficient=true only when the evidence adequately covers every aspect of the "
        "question; when false, corrective_queries must contain at most "
        "{max_queries} focused queries naming papers, methods, datasets, models, "
        "tables, figures or sections (never vague phrases); when true, use empty arrays. "
        "The verdict JSON must be the last thing in your output."
    ),
    "zh": (
        f"写完答案后，另起一行只输出 {VERDICT_MARKER}，并在其后的行里输出一个 JSON 对象："
        '{"sufficient":true|false,"insufficiency_reasons":["缺少哪些证据"],'
        '"corrective_queries":["可直接检索的补检 query"]}。'
        "只有当证据充分覆盖问题的每个方面时才把 sufficient 设为 true；为 false 时，"
        "corrective_queries 最多给 {max_queries} 个聚焦的 query，点名论文、方法、数据集、模型、"
        "表格、图或章节（不要用含糊的短语）；为 true 时给空数组。"
        "verdict JSON 必须是输出的最后内容。"
    ),
}


def build_synth_check_system(
    answer_language: str, max_corrective_queries: int, *, lang: PromptLang
) -> str:
    """以 enhanced 合成模板为基底，追加 verdict 契约（CRAG 式自检内嵌于合成调用）。"""
    return (
        _SYNTH_SYSTEM_ENHANCED[lang]
        + lang_instruction(answer_language, lang)
        + _VERDICT_RULE[lang].replace("{max_queries}", str(max_corrective_queries))
        + ACTION_GUIDANCE[lang]
    )


def build_synth_check_prompt(
    question: str,
    evidence: list[DocumentChunk],
    source_labels: dict[str, str] | None = None,
    *,
    lang: PromptLang,
) -> str:
    """与 answer_synthesis.synthesize_answer 同构的证据排版（[n] 编号 + 来源标注）。"""
    context = "\n\n---\n\n".join(
        f"[{i + 1}]{source_tag(chunk.doc_id, source_labels, lang)} "
        f"(ID: {chunk.chunk_id}) {evidence_text(chunk)}"
        for i, chunk in enumerate(evidence)
    )
    return context_prompt(lang, context, question)


def _str_items(value: object) -> list[str]:
    """宽容解析字符串列表（保序去重非空项）；非列表按单个字符串处理。"""
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def parse_synth_check(raw: str) -> SynthCheckResult:
    """按尾部 VERDICT_MARKER 切分正文与充分性 JSON。

    契约：正文为空（含只有 verdict 无正文）→ JsonContractError（触发重试）；
    标记缺失或 verdict JSON 无法解析 → fail-open：整段视为答案、sufficient=False
    （永不劣于 default 模式的单次合成）。
    """
    text = raw.strip()
    if text.startswith("```"):
        # 容忍模型把整体包进 fence：只剥外层，正文内部的 fence 不受影响。
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text:
        raise JsonContractError("empty synth output")
    marker_at = text.rfind(VERDICT_MARKER)
    if marker_at == -1:
        return SynthCheckResult(answer=text)
    answer = text[:marker_at].strip()
    if not answer:
        raise JsonContractError("empty answer body before verdict")
    tail = text[marker_at + len(VERDICT_MARKER) :].strip()
    try:
        obj = extract_json_object(tail)
    except JsonContractError:
        return SynthCheckResult(answer=answer)
    return SynthCheckResult(
        answer=answer,
        sufficient=obj.get("sufficient") is True,
        insufficiency_reasons=_str_items(obj.get("insufficiency_reasons")),
        corrective_queries=_str_items(obj.get("corrective_queries")),
        actions=parse_actions(obj.get("actions")),
    )


__all__ = [
    "VERDICT_MARKER",
    "SYNTH_CHECK_RETRY_SUFFIX",
    "PLAN_LITE_SYSTEM",
    "SynthCheckResult",
    "build_plan_lite_prompt",
    "parse_plan_lite",
    "build_synth_check_system",
    "build_synth_check_prompt",
    "parse_synth_check",
]
