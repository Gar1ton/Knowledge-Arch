"""Deep thinking 与 ask 共享的答案合成（pipelines 层）。

把「证据 → 带 [n] 引用的答案」抽为可复用单元，供 orchestrator 的 verification 闭环
与 api.ask 共用，避免重复合成逻辑。不含 lightrag 包装（那是 api.ask 展示层）；用户系统提示
只经 `with_user_system_prompt` 叠加在 default / graph / fulltext 路径，deep / enhanced 不叠加。
[n] 按 evidence 顺序编号，调用方据同序拼 sources，保证引用对齐。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from kacore.domain.llm_generation import STATUS_EMPTY, GenerationResult
from kacore.pipelines.evidence_views import evidence_text
from kacore.pipelines.prompt_language import PromptLang, prompt_lang

if TYPE_CHECKING:
    from kacore.adapters.llm import LLMAdapter
    from kacore.domain.models import DocumentChunk

# 每条证据可能来自不同来源文档；禁止跨来源张冠李戴的统一约束（防跨文档知识串线）。
_SOURCE_ISOLATION_RULE: dict[PromptLang, str] = {
    "en": (
        "Each evidence item is labeled with its source document (Source). "
        "Findings, methods and limitations from different sources MUST NOT be conflated or "
        "mis-attributed: attribute every claim only to its true source and cite it with that "
        "source's [n]. When the question is scoped to one specific paper, answer only what that "
        "paper's evidence supports; other sources may be used solely for explicitly named "
        "comparison, never silently merged into that paper's claims. "
    ),
    "zh": (
        "每条证据都标注了来源文档（来源）。不同来源的发现、方法与局限绝不能混淆或张冠李戴："
        "每个论断只归属于它真正的来源，并用该来源的 [n] 引用。问题限定在某一篇论文时，只回答"
        "该论文证据所支持的内容；其他来源只能用于明确点名的对比，绝不能悄悄并入该论文的论断。"
    ),
}

# 成段行文约束（v0.30.0 流畅性）：正文写成完整段落，杜绝碎片化单行 bullet 与流水长句。
_FLUENT_PROSE_RULE: dict[PromptLang, str] = {
    "en": (
        "Write the body in flowing, well-structured paragraphs (2-5 sentences each) separated by "
        "blank lines; use bullet lists only for genuinely enumerable items, never as fragmented "
        "one-line prose, and avoid run-on sentences. "
    ),
    "zh": (
        "正文写成连贯、结构清晰的段落（每段 2–5 句），段落之间空一行；只有真正可枚举的内容才用"
        "项目符号列表，不要把正文写成零碎的单行条目，也避免冗长的流水句。"
    ),
}

# enhanced/deep 共用的可读性约束：答案先行、自适应分节、术语解释、按论点组聚合引用。
_ACADEMIC_READABILITY_RULE: dict[PromptLang, str] = {
    "en": (
        "Begin with a concise, direct answer to the user's actual question before expanding. "
        "Use descriptive headings only when they clarify distinct parts of this specific "
        "question; do not force stock sections or begin with an inventory of retrieved sources "
        "and page numbers. Explain specialized terms in plain language at first use, and keep "
        "each paragraph focused on one main idea. Place citations after the smallest coherent "
        "claim group or paragraph; do not repeat the same citation after consecutive sentences "
        "unless needed to distinguish evidence. "
    ),
    "zh": (
        "先用简洁、直接的一段回答用户真正的问题，再展开。只有在能区分本问题的不同部分时才使用"
        "描述性小标题；不要硬套固定章节，也不要以罗列检索到的来源和页码开头。专业术语首次出现时"
        "用通俗语言解释，每段只讲一个核心观点。引用放在最小的连贯论断组或段落之后；除非需要区分"
        "证据，不要在相邻句子后重复同一引用。"
    ),
}

# 只依据资料作答的基本约束（default / graph 路径与基模板共用）。
_PLAIN_RAG_RULE: dict[PromptLang, str] = {
    "en": (
        "You are a helpful academic assistant. "
        "Answer the question based solely on the provided context. "
        "If the context is insufficient, state the limitation clearly and only answer what the "
        "context supports. "
        "Do not fill gaps with outside knowledge. "
    ),
    "zh": (
        "你是一名乐于助人的学术助手。只依据所给资料回答问题。资料不足时，明确说明局限，只回答"
        "资料能支持的部分。不要用外部知识填补空缺。"
    ),
}

_CITE_RULE: dict[PromptLang, str] = {
    "en": "Cite sources using [n] notation (e.g. [1], [2]). ",
    "zh": "用 [n] 标注引用来源（如 [1]、[2]）。",
}

# 与 api.ask 一致的合成 system 基模板（answer_language 指令追加在后）。
_SYNTH_SYSTEM_BASE: dict[PromptLang, str] = {
    lang: _PLAIN_RAG_RULE[lang] + _CITE_RULE[lang] + _FLUENT_PROSE_RULE[lang]
    + _SOURCE_ISOLATION_RULE[lang]
    for lang in ("en", "zh")
}

# Enhanced Recall：比 default 更有组织，但不采用 deep 的穷尽式报告长度。
_ENHANCED_INTRO: dict[PromptLang, str] = {
    "en": (
        "You are a careful academic assistant writing a moderately detailed, evidence-grounded "
        "answer. Answer based solely on the provided context; do not use outside knowledge. Cite "
        "supported claims with [n] notation and state genuine evidence limitations clearly. "
        "After the direct answer, develop the two to five most relevant dimensions when the "
        "question benefits from sections; for a narrow question, prefer a few clear paragraphs "
        "instead. Prioritize explanation and synthesis over exhaustively restating every "
        "retrieved passage. "
    ),
    "zh": (
        "你是一名严谨的学术助手，要写一篇详略适中、以证据为依据的回答。只依据所给资料作答，"
        "不使用外部知识。用 [n] 标注有证据支持的论断，并如实说明证据的真实局限。给出直接回答后，"
        "若问题适合分节，就展开最相关的两到五个维度；问题较窄时，宁可用几段清晰的文字。重在解释"
        "与综合，而不是逐条复述每段检索结果。"
    ),
}
_SYNTH_SYSTEM_ENHANCED: dict[PromptLang, str] = {
    lang: _ENHANCED_INTRO[lang] + _ACADEMIC_READABILITY_RULE[lang] + _FLUENT_PROSE_RULE[lang]
    + _SOURCE_ISOLATION_RULE[lang]
    for lang in ("en", "zh")
}

# Deep Thinking 专用：在严格 grounded 的前提下要求机制级、分维度、带跨实体对比的**完整**回答。
# 去压缩关键：① 按信息点/维度分节展开，每节充分展开、勿人为缩短（修复「单次合成短而拘谨」）；
# ② 允许「有据推断」——证据部分相关但未逐字命中问题措辞时给出带 hedge 的推断，而非一律判
#   「无证据」全盘否定（修复「部分可支持→完全无证据」误判）。硬约束（禁臆造/禁跨源）保留。
_DEEP_INTRO: dict[PromptLang, str] = {
    "en": (
        "You are a rigorous research assistant writing a COMPREHENSIVE, in-depth, report-style "
        "evidence-grounded answer. Answer based solely on the provided context; do not use "
        "outside knowledge; cite every supported claim or coherent claim group with [n] "
        "notation. Organize the answer into clearly-headed sections covering the distinct "
        "dimensions of the question (e.g. definition/background, mechanisms & methods, "
        "comparison across entities, timeline/trends, limitations, and a closing synthesis). "
        "Under each section develop the point fully, naming the specific mechanisms, techniques, "
        "datasets, models and terms that appear in the context rather than generic abstractions. "
        "Be thorough: when the evidence supports a point, expand it as far as the evidence allows "
        "and do NOT artificially shorten or compress; draw on every relevant retrieved source, "
        "not only the first few. "
        "When the question's framing (a claimed trend, shift, relationship or synthesis) is not "
        "stated verbatim in any single source but the evidence partially bears on it, do NOT "
        "refuse with 'no evidence'; instead synthesize a clearly hedged, evidence-grounded "
        "inference (e.g. 'the evidence partially supports …' / 'from [n] it can be inferred "
        "that …') and cite the partial evidence. Reserve an explicit 'no evidence' statement "
        "only for points the context genuinely does not touch at all. Never invent facts or "
        "cross-attribute sources. "
        "For comparison or 'shared X of A and B' questions, enumerate the concrete dimensions; "
        "under each dimension give each entity's specific mechanism with its citation, then add "
        "a synthesis line on how they align. "
    ),
    "zh": (
        "你是一名严谨的研究助手，要写一篇全面、深入、报告式且以证据为依据的回答。只依据所给资料"
        "作答，不使用外部知识；每个有证据支持的论断或连贯的论断组都用 [n] 标注引用。按问题的不同"
        "维度组织成带清晰小标题的章节（例如定义/背景、机制与方法、实体间对比、时间线/趋势、局限，"
        "以及收尾综合）。每节都要充分展开，点名资料中出现的具体机制、技术、数据集、模型和术语，"
        "而不是泛泛而谈。要详尽：证据支持某一点时，在证据允许的范围内尽量展开，不要人为缩短或"
        "压缩；用上所有相关的检索来源，而不只是前几条。"
        "当问题的设定（某种趋势、转变、关系或综合判断）在任何单一来源中都没有逐字出现，但证据"
        "部分涉及时，不要以「没有证据」拒答；而是给出措辞明确留有余地、以证据为依据的推断（例如"
        "「证据部分支持……」/「可据 [n] 推断……」），并引用这些部分证据。只有资料完全没有涉及的点，"
        "才明确说明「没有证据」。绝不编造事实，也不要张冠李戴来源。"
        "对比类或「A 与 B 共有的 X」类问题，要列出具体维度；每个维度下给出各实体的具体机制及其"
        "引用，再用一句话综合它们如何对应。"
    ),
}
_SYNTH_SYSTEM_DEEP: dict[PromptLang, str] = {
    lang: _DEEP_INTRO[lang] + _ACADEMIC_READABILITY_RULE[lang] + _FLUENT_PROSE_RULE[lang]
    + _SOURCE_ISOLATION_RULE[lang]
    for lang in ("en", "zh")
}

# 全文检索专用：上下文是**一整篇**文档，不是拼接的证据池。
# 故意不含 _SOURCE_ISOLATION_RULE——那条规则是防多篇证据张冠李戴的，单文档场景下没有可串的线，
# 硬塞进来只会让模型误以为还有别的来源。反过来要强调「覆盖全篇、别只答开头」。
_FULLTEXT_INTRO: dict[PromptLang, str] = {
    "en": (
        "You are a helpful academic assistant. The context below is the COMPLETE text of a "
        "single document. Answer the question based solely on this document; do not use outside "
        "knowledge. Draw on the whole document rather than only its opening sections, and when "
        "the question spans several parts of the text, cover each of them. "
        "If the document genuinely does not address the question, say so plainly instead of "
        "inventing an answer. "
        "Cite the document using [1] notation. "
    ),
    "zh": (
        "你是一名乐于助人的学术助手。下面的资料是单篇文档的完整全文。只依据这篇文档回答问题，"
        "不使用外部知识。要利用整篇文档，而不只是开头几节；问题涉及正文多个部分时，逐一覆盖。"
        "如果文档确实没有涉及该问题，就直接说明，不要编造答案。用 [1] 标注引用该文档。"
    ),
}
_SYNTH_SYSTEM_FULLTEXT: dict[PromptLang, str] = {
    lang: _FULLTEXT_INTRO[lang] + _FLUENT_PROSE_RULE[lang] for lang in ("en", "zh")
}

_LANG_INSTRUCTION: dict[PromptLang, dict[str, str]] = {
    "en": {
        "zh": "Answer in Chinese (中文).",
        "en": "Answer in English.",
        "auto": "Answer in the same language as the question.",
    },
    "zh": {
        "zh": "请用中文回答。",
        "en": "请用英文（English）回答。",
        "auto": "请使用与问题相同的语言回答。",
    },
}

_QUESTION_LABEL: dict[PromptLang, str] = {"en": "Question: ", "zh": "问题："}
_CONTEXT_LABEL: dict[PromptLang, str] = {"en": "Context:", "zh": "资料："}


def lang_instruction(answer_language: str, lang: PromptLang) -> str:
    """把 answer_language（auto/zh/en）翻成给 LLM 的一句语言指令，用 lang 对应的提示词语言写。

    公开导出而非私有：api.ask 的 graph / default 分支与 enhanced 合成共用。
    """
    table = _LANG_INSTRUCTION[lang]
    return table.get(answer_language, table["auto"])


def rag_rules(lang: PromptLang, answer_language: str, *, cite: bool) -> str:
    """default / graph 路径的合成约束：只依据资料作答（+ [n] 引用）+ 语言指令。"""
    return (
        _PLAIN_RAG_RULE[lang]
        + (_CITE_RULE[lang] if cite else "")
        + lang_instruction(answer_language, lang)
    )


def context_prompt(lang: PromptLang, context: str, question: str) -> str:
    """「资料 + 问题」的 user prompt（default / graph / deep / enhanced 合成共用排版）。"""
    return f"{_CONTEXT_LABEL[lang]}\n\n{context}\n\n{_QUESTION_LABEL[lang]}{question}"


def with_user_system_prompt(user_system_prompt: str, rag_rules: str) -> str:
    """把用户在「回答」节点写的系统提示（`flow.answer.system_prompt`）放在 RAG 约束之前。

    为空（或全空白）时原样返回 rag_rules——与旧版「人格关闭」时发送的 system 完全一致。
    放在前面而不是后面：RAG 约束（只依据证据、按 [n] 引用）是最后说的话，优先级更高。
    """
    text = (user_system_prompt or "").strip()
    return f"{text}\n\n{rag_rules}" if text else rag_rules


def source_tag(doc_id: str, source_labels: dict[str, str] | None, lang: PromptLang) -> str:
    """据 doc_id 取来源标签，拼成 `（来源：X）` / ` (Source: X)` 前缀；无映射时返回空串。

    防跨文档知识串线：每条证据带上其来源文档，模型才能区分多篇拼接的证据池。
    """
    if not source_labels:
        return ""
    label = source_labels.get(doc_id)
    if not label:
        return ""
    return f"（来源：{label}）" if lang == "zh" else f" (Source: {label})"


async def synthesize_answer(
    llm: LLMAdapter,
    question: str,
    evidence: list[DocumentChunk],
    answer_language: str = "auto",
    style: str = "default",
    source_labels: dict[str, str] | None = None,
) -> GenerationResult:
    """用证据合成带 [n] 引用的答案。

    契约：[n] 按 evidence 顺序编号（调用方据同序拼 sources）；evidence 为空返回
    `GenerationResult(text="", status=STATUS_EMPTY)`（由调用方决定兜底）；LLM 调用异常
    向上抛（由调用方处理，禁用 mock 兜底）。返回结构化结果而非裸字符串——调用方需要按
    status（reasoning_only/length/content_filter/…）分流，而不是只看 text 是否为空。
    style="deep" 选用机制级/分维度/带对比的 deep 合成模板，否则用通用模板。
    source_labels（doc_id→来源标签）给每条证据标注来源文档，防跨文档串线；None 时不标注。
    """
    if not evidence:
        return GenerationResult(text="", status=STATUS_EMPTY)
    lang = prompt_lang(answer_language, question)
    base = _SYNTH_SYSTEM_DEEP if style == "deep" else _SYNTH_SYSTEM_BASE
    system = base[lang] + lang_instruction(answer_language, lang)
    context = "\n\n---\n\n".join(
        f"[{i + 1}]{source_tag(chunk.doc_id, source_labels, lang)} {evidence_text(chunk)}"
        for i, chunk in enumerate(evidence)
    )
    user = context_prompt(lang, context, question)
    return await llm.generate_result(user, system_prompt=system, allow_mock=False)


async def synthesize_from_document(
    llm: LLMAdapter,
    question: str,
    document_text: str,
    *,
    title: str,
    answer_language: str = "auto",
    user_system_prompt: str = "",
) -> GenerationResult:
    """用**整篇文档**合成答案（全文检索模式）。

    契约：`document_text` **原样入 prompt，绝不截断**——全文检索的全部意义就在于模型看到全篇，
    在这里悄悄截断等于把功能变成一个更差的召回。因此超出模型上下文窗口时正确行为是**让调用
    失败**（`allow_mock=False` 保证不会掉进离线占位文本），由调用方转成明确错误告知用户，
    而不是返回半篇答案冒充完整回答。

    正文为空返回 `GenerationResult(text="", status=STATUS_EMPTY)`；LLM 调用异常向上抛。
    引用恒为 [1]（只有一个来源），调用方据此拼单条 sources。
    user_system_prompt 非空时放在全文约束之前（见 `with_user_system_prompt`）。
    """
    if not document_text.strip():
        return GenerationResult(text="", status=STATUS_EMPTY)
    lang = prompt_lang(answer_language, question)
    system = with_user_system_prompt(
        user_system_prompt, _SYNTH_SYSTEM_FULLTEXT[lang] + lang_instruction(answer_language, lang)
    )
    if lang == "zh":
        user = f"文档 [1]：{title}\n\n全文：\n\n{document_text}\n\n问题：{question}"
    else:
        user = f"Document [1]: {title}\n\nFull text:\n\n{document_text}\n\nQuestion: {question}"
    return await llm.generate_result(user, system_prompt=system, allow_mock=False)


__all__ = [
    "context_prompt",
    "lang_instruction",
    "rag_rules",
    "source_tag",
    "synthesize_answer",
    "synthesize_from_document",
    "with_user_system_prompt",
]
