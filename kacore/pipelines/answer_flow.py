"""问答链路的数据结构与回答文字（pipelines 层；P34 从 api.py 拆出）。

- `AskRequest`：一次提问规整后的全部输入，五个阶段（准备 → 取证据 → 补充证据 → 合成 → 收尾）都读它。
- `Evidence`：「取证据」交给后面各阶段的证据包；每个模式交同一种形状，补充证据与收尾因此不分模式。
- `Draft`：「合成」的产出。
- 其余是回答正文里的固定文字（降级摘录、提示、参考文献标题）与思考过程序列化，原样从 api.py 移来。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kacore.domain.llm_generation import STATUS_CONTENT_FILTER, STATUS_LENGTH, STATUS_REASONING_ONLY
from kacore.pipelines.prompt_language import prompt_lang, uses_chinese
from kacore.pipelines.retrieval_orchestrator import RetrievalScope
from kacore.utils.text_chunks import clip_at_sentence

if TYPE_CHECKING:
    from kacore.domain.models import DocumentChunk, SourceDocument
    from kacore.retrieval_modes import ModeSpec


@dataclass
class AskRequest:
    """一次提问规整后的输入。question 在「准备」阶段可能改写成独立问题；user_question 是原话。"""

    cid: str
    mode: str
    spec: ModeSpec
    question: str
    user_question: str
    collection: str | None
    scope: RetrievalScope | None
    top_k: int
    answer_language: str
    external: bool
    use_english_retrieval: bool
    use_context: bool
    candidate_k: int | None
    use_reranker: bool
    answer_scope: dict[str, str]
    started: float
    fulltext_doc: SourceDocument | None = None
    fulltext_content: str = ""
    standalone_question: str = ""
    retrieval_question: str = ""
    chat_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence:
    """取证据的产出。sources / chunks / context_parts 按 [n] 同序对齐；补充证据阶段会就地往后追加。

    stored_mode：存进聊天记录、返回给调用方的实际模式（由各模式策略或收尾按引擎推出）。
    event_meta：收尾时运行事件里该模式特有的字段（如全文的 doc_id / total_chars）。
    """

    chunks: list[DocumentChunk] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    context_parts: list[str] = field(default_factory=list)
    lightrag_context: str = ""
    engines: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    deep_outcome: Any = None
    stored_mode: str = ""
    event_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Draft:
    """合成的产出。citable：正文里的 [n] 是模型按证据顺序写下的引用，收尾才做 Harvard 改写。"""

    answer: str
    citable: bool
    generation_status: str = ""
    llm_started: float = 0.0  # 开始生成的时刻（monotonic），只用于收尾日志拆分检索 / 生成耗时


def build_scope(scope_type: str, scope_key: str, scope_library_id: str) -> RetrievalScope | None:
    """构造检索作用域；scope_type 为空返回 None（无 doc 级硬过滤）。"""
    if not scope_type:
        return None
    return RetrievalScope(
        scope_type=scope_type, scope_key=scope_key, library_id=scope_library_id
    )


def _uses_chinese(text: str) -> bool:
    """粗略判断文本是否包含中文，用于 answer_language=auto 的警告语言。"""
    return uses_chinese(text)


def answers_in_chinese(answer_language: str, question: str) -> bool:
    """答案正文里的固定文案（兜底说明、参考文献标题）跟随回答语言；auto 按提问语言判断。"""
    return prompt_lang(answer_language, question) == "zh"


def reference_heading(answer_language: str, question: str) -> str:
    return "参考文献" if answers_in_chinese(answer_language, question) else "References"


def serialize_deep_thinking(outcome: Any) -> dict:
    """把 DeepThinkingOutcome 序列化为前端可渲染的「思考过程」字典。

    与 orchestrator 实时进度的 live_detail 共用 deep_thinking_view 的底层序列化，
    从根上保证「实时进度格式 == 最终 thinking_trace 格式」。
    """
    from kacore.pipelines.deep_thinking_view import serialize_outcome

    return serialize_outcome(outcome)


def cited_external_ords(answer: str, sources: list[dict[str, Any]]) -> set[int]:
    """回答正文里真正引用到的外部来源（按 [n] 对回 source），供账本标记 cited。"""
    from kacore.pipelines.citation_rendering import cited_numbers

    numbers = cited_numbers(answer) if answer else set()
    return {int(s["ord"]) for s in sources
            if s.get("origin") == "external" and s.get("n") in numbers and "ord" in s}


def excerpt_answer(sources: list[dict], answer_language: str, question: str) -> str:
    """LLM 不可用的降级排版：引导行说明「未合成」+ 每条摘录带标题/页码、按句边界截断。

    不把句中硬切的原文碎片直接拼成正文（流畅性）；正文里的 `**[n] 标题**` 不是引用，不做改写。
    """
    excerpts = []
    for s in sources:
        meta = s.get("metadata") or {}
        page_info = f" (p. {meta['page_number']})" if meta.get("page_number") else ""
        excerpts.append(
            f"**[{s['n']}] {s['title']}**{page_info}\n{clip_at_sentence(s['text'], 300)}"
        )
    lead = (
        f"⚠️ LLM 暂不可用，未能合成回答。以下为检索到的 {len(sources)} 段原文摘录：\n\n"
        if answers_in_chinese(answer_language, question)
        else f"⚠️ The LLM is unavailable, so no answer could be written. Here are "
        f"{len(sources)} retrieved passages:\n\n"
    )
    return lead + "\n\n".join(excerpts)


def deep_warning_prefix(outcome: Any, answer_language: str, question: str) -> str:
    """为 deep thinking 证据不足/未验证答案生成正文警告前缀。"""
    zh = answer_language == "zh" or (answer_language == "auto" and _uses_chinese(question))
    if getattr(outcome, "degraded", False):
        reason = str(getattr(outcome, "degraded_reason", "") or "").strip()
        if zh:
            reason_part = f"原因：{reason}。" if reason else ""
            return (
                f"**提示：深度思考证据不足，已降级为基线证据。** {reason_part}"
                "以下回答仅基于当前检索片段，可能不完整。\n\n"
            )
        reason_part = f" Reason: {reason}." if reason else ""
        return (
            "**Note: Deep Thinking found insufficient evidence and fell back to baseline "
            "evidence.**"
            f"{reason_part} The answer below is limited to the currently retrieved "
            "passages and may be incomplete.\n\n"
        )
    missing = [
        str(item).strip()
        for item in (getattr(outcome, "verify_missing", []) or [])
        if str(item).strip()
    ]
    notes = [
        str(item).strip()
        for item in (getattr(outcome, "verify_notes", []) or [])
        if str(item).strip()
    ]
    if getattr(outcome, "verified", False):
        # 通过校验：含「有据推断」软项时给一行温和说明（不报数字、不全盘否定）；否则无前缀。
        if not notes:
            return ""
        if zh:
            return (
                "**提示：部分结论为基于证据的有限推断，已在文中标注。** "
                "详见下方「思考过程」。\n\n"
            )
        return (
            "**Note: Some conclusions are limited, evidence-grounded inferences "
            "(flagged inline).** See “Thinking process” below.\n\n"
        )
    if zh:
        if missing:
            return (
                "**提示：以下回答未完全通过证据校验。** "
                f"共 {len(missing)} 项缺失或未被支撑，详见下方「思考过程」。"
                "请将后文视为基于当前证据的有限回答。\n\n"
            )
        return (
            "**提示：以下回答尚未完成证据校验。** "
            "请将后文视为基于当前证据的有限回答。\n\n"
        )
    if missing:
        return (
            "**Note: The answer below did not fully pass evidence verification.** "
            f"{len(missing)} point(s) are missing or unsupported; see “Thinking process” below. "
            "Treat the rest as a limited answer based on the current evidence.\n\n"
        )
    return (
        "**Note: The answer below has not completed evidence verification.** "
        "Treat the rest as a limited answer based on the current evidence.\n\n"
    )


# v1.0.12：finish_reason/reasoning-only 等生成状态对用户可见的简短说明（中英双语）。
# 只覆盖「答案可能不完整/不是常规回答」这类需要用户知晓的状态，ok/empty/error/timeout
# 不在此处理（empty/error/timeout 要么已被上游兜底成占位答案，要么根本没有 answer）。
_GENERATION_STATUS_NOTICE_ZH: dict[str, str] = {
    STATUS_REASONING_ONLY: (
        "**提示：模型多次仅返回推理过程，通过追加指令才拿到最终答案，"
        "内容可能不如常规回答完整。**\n\n"
    ),
    STATUS_LENGTH: "**提示：模型输出因长度限制被截断，以下内容可能不完整。**\n\n",
    STATUS_CONTENT_FILTER: (
        "**提示：模型输出被内容策略过滤，以下回答可能不完整或已被替换。**\n\n"
    ),
}


_GENERATION_STATUS_NOTICE_EN: dict[str, str] = {
    STATUS_REASONING_ONLY: (
        "**Note: The model repeatedly returned only its reasoning; the final answer "
        "required an extra prompt and may be less complete than usual.**\n\n"
    ),
    STATUS_LENGTH: (
        "**Note: The model's output was truncated due to length limits; "
        "the answer below may be incomplete.**\n\n"
    ),
    STATUS_CONTENT_FILTER: (
        "**Note: The model's output was filtered by content policy; "
        "the answer below may be incomplete or altered.**\n\n"
    ),
}


def generation_status_notice(
    generation_status: str, answer_language: str, question: str
) -> str:
    """按 GenerationResult.status 给出用户可见的简短说明；未命中状态返回空串。"""
    if not generation_status:
        return ""
    zh = answer_language == "zh" or (answer_language == "auto" and _uses_chinese(question))
    table = _GENERATION_STATUS_NOTICE_ZH if zh else _GENERATION_STATUS_NOTICE_EN
    return table.get(generation_status, "")


def compute_answer_notice(
    outcome: Any | None,
    answer_language: str,
    question: str,
    generation_status: str = "",
) -> str:
    """deep/enhanced 证据不足/未验证 + 生成状态异常时的提示文本（结构化字段，不再拼进正文开头）。

    v0.30.0：由「prepend 到 answer」改为响应独立的 answer_notice 字段——正文与告警解耦，
    实时渲染端自行决定呈现位置。存库时以分隔线并入答案（见 ask()），保证历史回放不丢提示
    ——轻微双轨是「实时结构化 vs 历史自包含」的有意取舍。
    v1.1.0：渲染出口（Ask 气泡 / 存库回放）统一把告警置于正文**之前**。
    读完整篇才发现「本回答未通过证据校验」为时已晚，告警的价值在于先于正文影响阅读预期。
    v1.0.12：追加 generation_status 分支——reasoning_only/length/content_filter 时即便
    evidence/verification 都正常，也要让用户知道这次生成本身不寻常。
    """
    status_notice = generation_status_notice(generation_status, answer_language, question).strip()
    outcome_notice = (
        deep_warning_prefix(outcome, answer_language, question).strip()
        if outcome is not None
        else ""
    )
    return "\n\n".join(part for part in (status_notice, outcome_notice) if part)


# 已分配对话模型但合成调用失败：答案是检索摘录而非模型回答（摘录正文自带说明，不再加提示）。
GENERATION_LLM_UNAVAILABLE = "llm_unavailable"


__all__ = [
    "GENERATION_LLM_UNAVAILABLE",
    "AskRequest",
    "Draft",
    "Evidence",
    "answers_in_chinese",
    "build_scope",
    "cited_external_ords",
    "compute_answer_notice",
    "deep_warning_prefix",
    "excerpt_answer",
    "generation_status_notice",
    "reference_heading",
    "serialize_deep_thinking",
]
