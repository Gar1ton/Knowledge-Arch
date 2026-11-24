"""问答链路里各模式自己的两段：取证据与合成（mixin，组合进 KnowledgeRepositoryApi）。

每个模式一个取证据策略，都交同一种 `Evidence`；合成按写法分三种：带引用（默认 / 增强 / 深度 /
图谱混合）、只用图谱（不带引用）、整篇文档。阶段之间的其他事（联网补充、引用改写、存库、事件、
返回结果）在 `api_answer.AskApiMixin`，不在这里写。

新增模式：在 `retrieval_modes.MODES` 写能力声明，在下面两张分派表各登记一个方法。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from kacore.answer_errors import (
    FullTextGenerationFailedError,
    GraphMixedQueryError,
    LightRAGNotReadyError,
)
from kacore.domain.llm_generation import STATUS_CONTENT_FILTER, STATUS_LENGTH
from kacore.pipelines.answer_flow import (
    GENERATION_LLM_UNAVAILABLE,
    AskRequest,
    Draft,
    Evidence,
    answers_in_chinese,
    excerpt_answer,
)
from kacore.pipelines.answer_synthesis import context_prompt, rag_rules, with_user_system_prompt
from kacore.pipelines.prompt_language import prompt_lang
from kacore.retrieval_modes import (
    MODE_DEEP_THINKING,
    MODE_DEFAULT,
    MODE_ENHANCED,
    MODE_FULLTEXT,
    MODE_GRAPH_MIXED,
    MODE_GRAPH_ONLY,
)
from kacore.utils.text_chunks import clip_at_sentence

logger = logging.getLogger("KnowledgeRepositoryApi")

Progress = Callable[..., None]

# 模式 → (取证据方法, 合成方法)。能力（要不要集合 / 文档、能否联网…）在 retrieval_modes.MODES。
_GATHER = {
    MODE_DEFAULT: "_gather_retrieval",
    MODE_ENHANCED: "_gather_retrieval",
    MODE_DEEP_THINKING: "_gather_retrieval",
    MODE_GRAPH_MIXED: "_gather_retrieval",
    MODE_GRAPH_ONLY: "_gather_graph_only",
    MODE_FULLTEXT: "_gather_document",
}
_SYNTHESIZE = {
    MODE_DEFAULT: "_synthesize_cited",
    MODE_ENHANCED: "_synthesize_cited",
    MODE_DEEP_THINKING: "_synthesize_cited",
    MODE_GRAPH_MIXED: "_synthesize_cited",
    MODE_GRAPH_ONLY: "_synthesize_graph",
    MODE_FULLTEXT: "_synthesize_document",
}


def _engines_mode(engines: list[str], *, graph: bool) -> str:
    """按实际用到的引擎给出存库 / 返回用的模式名。"""
    if graph:
        if "milvus" in engines:
            return "milvus_lightrag"
        if "sqlite_lexical" in engines:
            return "lexical_lightrag"
        return "lightrag"
    if "milvus" in engines:
        return "milvus"
    if "sqlite_lexical" in engines:
        return "sqlite_lexical"
    return "none"


class AnswerModesMixin:
    _source_store: Any
    _llm_adapter: Any
    _retrieval_orchestrator: Any
    _deep_thinking_orchestrator: Any
    _enhanced_recall_orchestrator: Any
    _reranker: Any

    async def _gather(self, req: AskRequest, progress: Progress) -> Evidence:
        """取证据：先做能力表声明的前置检查（图谱是否就绪），再交给该模式的策略。"""
        if req.spec.uses_graph:
            readiness = await self.get_lightrag_readiness(  # type: ignore[attr-defined]
                req.collection or ""
            )
            if not readiness["ready"]:
                raise LightRAGNotReadyError(
                    req.collection or "",
                    readiness["reason"],
                    build_available=readiness["build_available"],
                )
        strategy = getattr(self, _GATHER[req.mode])
        evidence: Evidence = await strategy(req, progress)
        return evidence

    async def _synthesize(
        self, req: AskRequest, ev: Evidence, summary: dict[str, Any] | None, progress: Progress
    ) -> Draft:
        writer = getattr(self, _SYNTHESIZE[req.mode])
        draft: Draft = await writer(req, ev, summary, progress)
        return draft

    # ── 取证据 ──────────────────────────────────────────────

    async def _gather_retrieval(self, req: AskRequest, progress: Progress) -> Evidence:
        """召回类模式（默认 / 增强 / 深度 / 图谱混合）：召回分块并按 [n] 编号成来源。"""
        mode = req.mode
        chunks: list[Any] = []
        engines: list[str] = []
        fallback_reason: str | None = None
        milvus_fallback_reason = await self._milvus_retrieval_fallback_reason()  # type: ignore[attr-defined]

        # deep_thinking / enhanced 两条编排路径产出 DeepThinkingOutcome，共享同一下游：
        # 来源组装、deep 风格兜底合成、告警、thinking_trace 序列化。
        deep_outcome = None
        if mode == MODE_DEEP_THINKING and self._deep_thinking_orchestrator is None:
            raise RuntimeError("DeepThinkingOrchestrator is not configured")
        if mode == MODE_ENHANCED and self._enhanced_recall_orchestrator is None:
            raise RuntimeError("EnhancedRecallOrchestrator is not configured")
        if mode == MODE_DEEP_THINKING:
            progress("deep_thinking", 20)
            deep_outcome = await self._deep_thinking_orchestrator.run(
                req.collection or "",
                req.retrieval_question,
                req.scope,
                progress=progress,
                answer_language=req.answer_language,
                answer_question=req.question,
            )
            chunks = list(deep_outcome.evidence)
            engines.append("deep_thinking")
        elif mode == MODE_ENHANCED:
            progress("enhanced", 15)
            deep_outcome = await self._enhanced_recall_orchestrator.run(
                req.collection or "",
                req.retrieval_question,
                req.scope,
                progress=progress,
                answer_language=req.answer_language,
                answer_question=req.question,
            )
            chunks = list(deep_outcome.evidence)
            engines.append("enhanced_recall")
        else:
            progress("vector_search", 20)
            # 一次召回覆盖整个范围：有 scope 时按 doc 白名单（子树 / 条目 / 文档），
            # 无集合时全局；编排器内部的向量与词法通道都按同一范围取候选。
            if self._retrieval_orchestrator is None:
                fallback_reason = fallback_reason or milvus_fallback_reason
            else:
                try:
                    outcome = await self._retrieval_orchestrator.retrieve_with_outcome(
                        req.collection or "",
                        req.retrieval_question,
                        req.top_k,
                        req.scope,
                        candidate_k=req.candidate_k,
                        reranker=self._reranker if req.use_reranker else None,
                    )
                    chunks = list(outcome.chunks)
                    engines.extend(outcome.engines)
                    fallback_reason = fallback_reason or outcome.fallback_reason
                except Exception as exc:
                    logger.warning("Ask retrieval failed for %r: %s", req.collection or "*", exc)
                    fallback_reason = fallback_reason or f"collection_error:{req.collection or '*'}"

        lightrag_context = ""
        if mode == MODE_GRAPH_MIXED:
            progress("lightrag_context", 50)
            try:
                if self._retrieval_orchestrator is None:
                    raise RuntimeError("RetrievalOrchestrator is not configured")
                lightrag_context = await self._retrieval_orchestrator.retrieve_lightrag_context(
                    req.collection or "", req.question, req.scope
                )
                engines.append("lightrag")
            except Exception as exc:
                logger.warning(
                    "LightRAG graph-mixed retrieval failed [%s]: %s", req.collection, exc
                )
                raise GraphMixedQueryError(req.collection or "", str(exc)) from exc

        progress("rrf_fusion", 65)

        sources = []
        context_parts = []
        doc_cache: dict[str, Any] = {}
        meta_cache: dict[str, dict[str, Any]] = {}
        for i, chunk in enumerate(chunks):
            n = i + 1
            source = await self._build_source_entry(  # type: ignore[attr-defined]
                chunk, n, doc_cache, meta_cache
            )
            sources.append(source)
            has_page = chunk.metadata and "page_number" in chunk.metadata
            page_info = f" (Page {chunk.metadata['page_number']})" if has_page else ""
            context_parts.append(f"[{n}] {source['title']}{page_info}\n{chunk.text}")

        stored_mode = (
            deep_outcome.actual_mode
            if deep_outcome is not None
            else _engines_mode(engines, graph=mode == MODE_GRAPH_MIXED)
        )
        return Evidence(
            chunks=chunks, sources=sources, context_parts=context_parts,
            lightrag_context=lightrag_context, engines=engines, fallback_reason=fallback_reason,
            deep_outcome=deep_outcome, stored_mode=stored_mode,
        )

    async def _gather_graph_only(self, req: AskRequest, progress: Progress) -> Evidence:
        """只用图谱：跳过向量 / 词法召回，只取图谱上下文；没有可编号的来源。"""
        await self._milvus_retrieval_fallback_reason()  # type: ignore[attr-defined]
        progress("lightrag_context", 30)
        try:
            if self._retrieval_orchestrator is None:
                raise RuntimeError("RetrievalOrchestrator is not configured")
            context = await self._retrieval_orchestrator.retrieve_lightrag_context(
                req.collection or "", req.question, req.scope
            )
        except Exception as exc:
            logger.warning("LightRAG graph_only retrieval failed [%s]: %s", req.collection, exc)
            raise GraphMixedQueryError(
                req.collection or "", str(exc), mode=MODE_GRAPH_ONLY
            ) from exc
        return Evidence(lightrag_context=context, engines=["lightrag"],
                        stored_mode="lightrag_only")

    async def _gather_document(self, req: AskRequest, progress: Progress) -> Evidence:
        """全文：整篇正文已在入口读好；来源恒为一条文档级条目（不是分块，in-text 短引不带页码）。"""
        progress("fulltext_load", 30)
        doc = req.fulltext_doc
        assert doc is not None  # 能力表 needs_doc：入口已读出整篇
        source = await self._build_document_source_entry(  # type: ignore[attr-defined]
            doc.doc_id, 1, {}, {}
        )
        # text 用 clip_at_sentence 而非整篇正文——sources 会随聊天记录落库，几十万字符会撑爆。
        source["text"] = clip_at_sentence(req.fulltext_content, 300)
        return Evidence(
            sources=[source], engines=[MODE_FULLTEXT], stored_mode=MODE_FULLTEXT,
            event_meta={"doc_id": doc.doc_id, "total_chars": len(req.fulltext_content)},
        )

    # ── 合成 ────────────────────────────────────────────────

    async def _synthesize_cited(
        self, req: AskRequest, ev: Evidence, summary: dict[str, Any] | None, progress: Progress
    ) -> Draft:
        """带引用的合成：deep / enhanced 的草稿优先，否则按证据写一稿；模型不可用时排版摘录。"""
        question, answer_language, mode = req.question, req.answer_language, req.mode
        chunks, sources, context_parts = ev.chunks, ev.sources, ev.context_parts
        lightrag_context, deep_outcome = ev.lightrag_context, ev.deep_outcome
        # deep / enhanced 的草稿只用了库内证据，有外部证据时作废，由下方「deep 风格合成」重写一稿。
        has_external = bool(summary and summary.get("used"))

        progress("llm_generate", 80)
        t_llm = time.monotonic()

        has_evidence = bool(context_parts or lightrag_context)
        deep_answer = (
            deep_outcome.answer if deep_outcome is not None and not has_external else None
        )
        generation_status = (
            deep_outcome.answer_generation_status if deep_outcome is not None else ""
        )
        if (
            not deep_answer
            and deep_outcome is not None
            and self._llm_adapter is not None
            and chunks
        ):
            # deep 模式但 verify 关闭/synth 失败：仍走 deep 合成，避免答案深度退回普通风格；
            # 失败则优雅退回下方通用合成。[n] 与 sources 同序对齐（均按 chunks 顺序）。
            from kacore.pipelines.answer_synthesis import synthesize_answer

            # 每条证据标注来源文档（Zotero 优先短引「作者 年份」，否则标题），防跨文档串线。
            source_labels = {
                s["doc_id"]: (s.get("citation") or s["title"]) for s in sources
            }
            try:
                deep_result = await synthesize_answer(
                    self._llm_adapter,
                    question,
                    chunks,
                    answer_language,
                    style="deep",
                    source_labels=source_labels,
                )
                deep_answer = deep_result.text
                generation_status = deep_result.status
            except Exception as exc:
                logger.warning("deep fallback synth failed, falling back to generic: %s", exc)

        # citable 标记「这段正文里的 [n] 是 LLM 按证据顺序写下的引用」，只有它为真才做
        # Harvard 改写。降级分支自己排版的 `**[1] 标题**` 不是引用，改写会毁掉版式。
        citable = False
        if deep_answer:
            # deep 答案（verification 闭环合成或上面的 deep fallback 合成）直接用。
            answer = deep_answer
            citable = True
        elif self._llm_adapter is not None and has_evidence:
            llm_failed = False
            lang = prompt_lang(answer_language, question)
            rules = rag_rules(lang, answer_language, cite=True)
            # deep / enhanced 的兜底合成保持原样，只有普通检索路径叠加用户系统提示。
            system_prompt = (
                rules
                if mode in {MODE_DEEP_THINKING, MODE_ENHANCED}
                else with_user_system_prompt(
                    self._answer_system_prompt(), rules  # type: ignore[attr-defined]
                )
            )

            graph_label = "[LightRAG 图谱资料]" if lang == "zh" else "[LightRAG Context]"
            graph_header = (
                f"{graph_label}\n{lightrag_context}\n\n---\n\n" if lightrag_context else ""
            )
            user_prompt = context_prompt(
                lang, graph_header + "\n\n---\n\n".join(context_parts), question
            )
            # 离线占位只留给「没分配对话模型」：已分配模型但这次调用失败（断网 / 5xx / 超时 /
            # 空响应）时，占位文本会冒充一段正常答案（P30：评测断网期间 8 题拿到占位）。
            # 改走下方的摘录排版，并以 generation_status=llm_unavailable 结构化告知调用方。
            try:
                generic_result = await self._llm_adapter.generate_result(
                    user_prompt,
                    system_prompt=system_prompt,
                    allow_mock=not getattr(self._llm_adapter, "configured", True),
                )
            except RuntimeError as exc:
                logger.warning("Answer synthesis failed; returning excerpts: %s", exc)
                llm_failed = True
                generation_status = GENERATION_LLM_UNAVAILABLE
            if not llm_failed:
                answer = generic_result.text
                citable = True
                # 未分配模型时 allow_mock=True：空文本状态会被离线占位覆盖，不能把「模型可能
                # 不完整」的提示挂在占位答案上；只有 length/content_filter 通常仍带着真实
                # （哪怕不完整）的文本，值得透给用户。
                if generic_result.status in (STATUS_LENGTH, STATUS_CONTENT_FILTER):
                    generation_status = generic_result.status
            elif context_parts:
                answer = excerpt_answer(sources, answer_language, question)
            else:
                answer = lightrag_context
        elif context_parts:
            answer = excerpt_answer(sources, answer_language, question)
        elif lightrag_context:
            answer = lightrag_context
        else:
            answer = (
                "未在知识库中找到与该问题相关的内容。请尝试其他关键词或上传相关文档。"
                if answers_in_chinese(answer_language, question)
                else "Nothing related to this question was found in the knowledge base. "
                "Try other keywords or upload related documents."
            )
        return Draft(answer, citable, generation_status, llm_started=t_llm)

    async def _synthesize_graph(
        self, req: AskRequest, ev: Evidence, summary: dict[str, Any] | None, progress: Progress
    ) -> Draft:
        """只用图谱：按图谱上下文作答，不带 [n] 引用（没有编号的来源）。"""
        progress("llm_generate", 80)
        t_llm = time.monotonic()
        question, context = req.question, ev.lightrag_context
        if self._llm_adapter is not None and context:
            lang = prompt_lang(req.answer_language, question)
            system_prompt = with_user_system_prompt(
                self._answer_system_prompt(),  # type: ignore[attr-defined]
                rag_rules(lang, req.answer_language, cite=False),
            )
            user_prompt = context_prompt(lang, context, question)
            answer = await self._llm_adapter.generate(user_prompt, system_prompt=system_prompt)
        else:
            answer = context or (
                "未在知识图谱中找到与该问题相关的内容。"
                if answers_in_chinese(req.answer_language, question)
                else "Nothing related to this question was found in the knowledge graph."
            )
        return Draft(answer, False, llm_started=t_llm)

    async def _synthesize_document(
        self, req: AskRequest, ev: Evidence, summary: dict[str, Any] | None, progress: Progress
    ) -> Draft:
        """整篇文档 → 答案。契约见 `synthesize_from_document`：正文不截断，超窗时让调用失败
        而非返回半篇答案。"""
        from kacore.pipelines.answer_synthesis import synthesize_from_document

        doc = req.fulltext_doc
        assert doc is not None
        content, question = req.fulltext_content, req.question
        progress("llm_generate", 60)
        t_llm = time.monotonic()
        if self._llm_adapter is None:
            # 明确告知无法作答，绝不把整篇正文当答案倒回去（那既没回答问题，又会撑爆存库）。
            answer = (
                "⚠️ LLM 暂不可用，无法基于全文作答。请检查 LLM 配置后重试。"
                if answers_in_chinese(req.answer_language, question)
                else "⚠️ The LLM is unavailable, so the full text could not be answered. "
                "Check the LLM settings and try again."
            )
            return Draft(answer, False, llm_started=t_llm)
        try:
            result = await synthesize_from_document(
                self._llm_adapter,
                question,
                content,
                title=doc.title or doc.doc_id,
                answer_language=req.answer_language,
                user_system_prompt=self._answer_system_prompt(),  # type: ignore[attr-defined]
            )
        except Exception as exc:
            raise FullTextGenerationFailedError(doc.doc_id, len(content), str(exc)) from exc
        status = result.status if result.status in (STATUS_LENGTH, STATUS_CONTENT_FILTER) else ""
        if not result.text.strip():
            raise FullTextGenerationFailedError(
                doc.doc_id,
                len(content),
                f"LLM returned no answer for a {len(content)}-char document "
                f"(status={result.status or 'empty'}); the full text likely exceeds "
                "the model's context window",
            )
        return Draft(result.text, True, status, llm_started=t_llm)


__all__ = ["AnswerModesMixin"]
