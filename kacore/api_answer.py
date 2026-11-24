"""回答生成相关的 API 子门面（mixin，组合进 KnowledgeRepositoryApi）。

- `AnswerPromptMixin`：读取用户在数据流「回答」节点填写的系统提示（`flow.answer.system_prompt`，
  即 `AskAgentConfig.system_prompt`）。每次调用现取，节点里改完立刻对下一次提问生效。
- `AskApiMixin`：问答链路（P34 起）。每种检索模式都走同一组阶段：

      入口校验（参数 → AskRequest）→ 准备（追问改写、检索词翻译）→ 取证据（各模式策略）
      → 补充证据（联网补充）→ 合成（各模式写法）→ 收尾（引用改写、提示、存库、事件、返回）

  模式之间的差别只在「取证据」与「合成」两处（`api_answer_modes.AnswerModesMixin`），以及
  `retrieval_modes.MODES` 能力表里的声明；其余阶段不判断模式名。以前三条路各写一遍收尾、
  新功能只接在主干上，旁路的模式就会悄悄漏掉（3.0.0-preview.1 的全文 / 只用图谱忽略联网开关）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from kacore.answer_errors import AskCancelledError, AskTaskTimeoutError
from kacore.pipelines.answer_flow import (
    AskRequest,
    Draft,
    Evidence,
    build_scope,
    cited_external_ords,
    compute_answer_notice,
    reference_heading,
    serialize_deep_thinking,
)
from kacore.pipelines.citation_rendering import annotate_sources, render_answer_with_references
from kacore.pipelines.prompt_language import uses_chinese
from kacore.pipelines.retrieval_orchestrator import SCOPE_DOCUMENT, RetrievalScope
from kacore.providers.thinking import model_thinking as thinking_flag
from kacore.retrieval_modes import MODES, invalid_mode_message, normalize_retrieval_mode
from kacore.utils.usage_ledger import current_usage, usage_request

if TYPE_CHECKING:
    from kacore.config import Config

logger = logging.getLogger("KnowledgeRepositoryApi")

Progress = Callable[..., None]


class AnswerPromptMixin:
    _config: Config | None

    def _answer_system_prompt(self) -> str:
        """用户系统提示；未配置或为空时返回空串（ask 不叠加任何内容）。"""
        if self._config is None:
            return ""
        return self._config.get_ask_agent_config().system_prompt.strip()


class AskApiMixin:
    """问答链路的阶段编排。依赖（_source_store / _llm_adapter / _runtime_events …）由宿主类提供；
    「取证据」`_gather` 与「合成」`_synthesize` 由 AnswerModesMixin 提供。"""

    _source_store: Any
    _llm_adapter: Any
    _runtime_events: Any
    _progress_store: Any
    _tool_distributor: Any

    @usage_request
    async def ask(
        self,
        question: str,
        collection: str | None = None,
        top_k: int = 6,
        conversation_id: str | None = None,
        retrieval_mode: str = "default",
        use_english_retrieval: bool = False,
        use_context: bool = False,
        answer_language: str = "auto",
        scope_type: str = "",
        scope_key: str = "",
        scope_library_id: str = "",
        candidate_k: int | None = None,
        use_reranker: bool = False,
        doc_id: str | None = None,
        confirmed: bool = False,
        model_thinking: bool = True,
        external: bool = False,
    ) -> dict:
        """Retrieve evidence and generate one final answer.

        external=True（3.0.0 联网补充，对话栏开关）：库内检索之后再去学术索引找外部文献，核验后作为
        追加证据一起合成，编号接在库内来源后面；结果与聊天记录带 `external` 摘要，出处写进账本。
        能力表里 supports_external=False 的模式（全文、只用图谱）不联网：开关开着时结果与聊天记录带
        `external: {"status": "unavailable", "reason": "mode"}`，界面据此说明没运行；关着时不带。

        candidate_k / use_reranker：研究路径用——候选池宽召回 + cross-encoder 重排再取 top_k
        （仅 default 模式生效；默认 answer top_k 不变）。reranker 为 passthrough 时自动退回 RRF。

        use_context：多轮对话。本会话已有问答时先把追问改写成独立问题（检索与生成都用它），
        结果里带 `standalone_question`；聊天记录保存用户原话。改写失败退回原问题。

        doc_id / confirmed：全文检索（`retrieval_mode="fulltext"`）用——把该文档整篇 clean.md
        送入 LLM，**不截断**。正文超过 `FULLTEXT_CONFIRM_THRESHOLD_CHARS` 且未 `confirmed`
        时抛 `FullTextConfirmationRequiredError`（询问而非失败），调用方取得用户明确同意后
        带 `confirmed=True` 原样重发。
        其他模式下 doc_id 把检索限定在这一篇（`document` 作用域，覆盖 collection / scope_*）：
        按它的主归属集合召回、只放行该 doc_id；图谱档位不支持单篇，抛 ValueError。

        model_thinking=False：本次提问内所有模型调用尽量关闭模型自带的推理（参数按连接换算，
        见 `providers/thinking.py`）；与 deep_thinking 检索档位无关。
        """
        req = await self._ask_request(
            question, collection=collection, top_k=top_k, conversation_id=conversation_id,
            retrieval_mode=retrieval_mode, use_english_retrieval=use_english_retrieval,
            use_context=use_context, answer_language=answer_language, scope_type=scope_type,
            scope_key=scope_key, scope_library_id=scope_library_id, candidate_k=candidate_k,
            use_reranker=use_reranker, doc_id=doc_id, confirmed=confirmed, external=external,
        )
        cid = req.cid
        self._runtime_events.start(
            category="llm",
            operation="ask",
            operation_id=cid,
            msg="Ask started",
            metadata={
                "conversation_id": cid,
                "collection": req.collection or "all",
                "mode": req.mode,
                "top_k": req.top_k,
            },
        )
        logger.info(
            "ask: mode=%s collection=%r top_k=%d question=%r",
            req.mode,
            req.collection,
            req.top_k,
            req.question[:80],
        )
        progress = self._ask_progress(req)
        progress("embed_query", 0)

        async def _run() -> dict:
            # 在任务自己的上下文里设：只作用于这一问（含派生的子任务），不漏到别的请求。
            thinking_flag.set(model_thinking)
            await self._ask_prepare(req)
            result = await self._ask_answer(req, progress)
            result["scope"] = req.answer_scope
            if req.standalone_question:
                result["standalone_question"] = req.standalone_question
            return result

        # 不限时也走任务：登记后清空聊天才能中断它。
        task = asyncio.create_task(_run())
        self._track_ask_task(cid, task)  # type: ignore[attr-defined]
        timeout_seconds = self._ask_task_timeout_seconds()  # type: ignore[attr-defined]
        done, _pending = await asyncio.wait(
            {task}, timeout=timeout_seconds if timeout_seconds > 0 else None
        )
        if task in done:
            if task.cancelled():
                raise AskCancelledError(cid)
            return task.result()

        # 不取消 task：非破坏式放弃等待，后台继续跑完并照常写 chat_history。前端超时/断线后
        # 可通过 /api/ask/progress 与 /api/chat/history 找回这份迟到的答案；唯一会取消它的是
        # cancel_ask()（清空聊天）：此时不再写库。
        task.add_done_callback(
            lambda t: self._on_late_ask_result(cid, req.mode, t)  # type: ignore[attr-defined]
        )
        elapsed = time.monotonic() - req.started
        logger.warning(
            "ask task exceeded the overall timeout of %ss "
            "(elapsed %.1fs, mode=%s, conversation_id=%s): stopped waiting without cancelling; "
            "the task keeps running and will be saved to chat_history. The frontend can keep "
            "polling /api/ask/progress and /api/chat/history for the late answer.",
            timeout_seconds,
            elapsed,
            req.mode,
            cid,
        )
        self._runtime_events.progress(
            operation_id=cid,
            category="llm",
            operation="ask",
            phase="task_timeout",
            percent=0,
            msg="Ask task exceeded the overall timeout; still running in the background",
            metadata={
                "conversation_id": cid,
                "mode": req.mode,
                "timeout_seconds": timeout_seconds,
            },
        )
        raise AskTaskTimeoutError(cid, timeout_seconds)

    # ── 入口校验 ────────────────────────────────────────────

    async def _ask_request(
        self, question: str, *, collection: str | None, top_k: int,
        conversation_id: str | None, retrieval_mode: str, use_english_retrieval: bool,
        use_context: bool, answer_language: str, scope_type: str, scope_key: str,
        scope_library_id: str, candidate_k: int | None, use_reranker: bool,
        doc_id: str | None, confirmed: bool, external: bool,
    ) -> AskRequest:
        """参数 → AskRequest。规则全部来自能力表；全文门禁也在这里（见下方注释）。"""
        scope = build_scope(scope_type, scope_key, scope_library_id)
        mode, used_legacy_mode = normalize_retrieval_mode(retrieval_mode)
        if used_legacy_mode:
            logger.warning(
                "retrieval_mode='high_precision' is deprecated; use 'graph_mixed' instead"
            )
        spec = MODES.get(mode)
        if spec is None:
            raise ValueError(invalid_mode_message())
        # 单篇文档范围（不需要整篇的模式）：dense 召回按 workspace 分库，故以该文档的主归属集合
        # 召回，再由 document scope 只放行这一篇。
        scope_doc = None
        if doc_id and not spec.needs_doc:
            if not spec.single_doc:
                raise ValueError(f"{mode} retrieval cannot be limited to one document")
            scope_doc = await self._source_store.get_document(doc_id)
            if scope_doc is None:
                raise ValueError(f"Document not found: {doc_id}")
            scope = RetrievalScope(
                scope_type=SCOPE_DOCUMENT, scope_key=doc_id, library_id=scope_doc.library_id
            )
            collection = scope_doc.collection or None
        # 未显式传 scope 但选了集合：默认按「选中集合 + 所有子目录」检索（含后代），
        # 由 coll_key 派生 collection scope，覆盖统一树的整棵子树。
        if scope is None and collection:
            selected = await self._source_store.get_collection_by_name(collection)
            if selected is not None:
                scope = RetrievalScope(
                    scope_type="collection",
                    scope_key=selected.coll_key,
                    library_id=selected.library_id,
                )
        # 图谱按集合建 workspace，必须有具体 collection；其余档位允许为空（全局）。
        if spec.needs_collection and not collection:
            raise ValueError(f"{mode} retrieval requires a collection")
        if answer_language not in {"auto", "zh", "en"}:
            answer_language = "auto"

        # 全文门禁刻意放在 runtime_events.start() 之前：① 被确认门拦下时不留一个永不结束的
        # 「Ask 已开始」事件；② 不白跑检索词翻译（全文模式不做召回）；③ 确认属于入参校验语义。
        fulltext_doc = None
        fulltext_content = ""
        if spec.needs_doc:
            if not doc_id:
                raise ValueError(f"{mode} retrieval requires a doc_id")
            fulltext_doc, fulltext_content = await self._load_fulltext_document(  # type: ignore[attr-defined]
                doc_id
            )
            self._require_fulltext_confirmation(  # type: ignore[attr-defined]
                fulltext_doc, fulltext_content, len(fulltext_content), confirmed
            )

        # 这一轮问答实际用的知识范围：存进聊天记录、随结果返回，界面按它标注每轮回答，
        # 不随之后的选中变化。全文模式与单篇范围按文档，其余按集合（空 = 全部集合）。
        answer_scope: dict[str, str] = {"collection": collection or ""}
        scoped_doc = fulltext_doc or scope_doc
        if scoped_doc is not None:
            answer_scope["doc_id"] = scoped_doc.doc_id
            answer_scope["doc_title"] = scoped_doc.title or ""

        return AskRequest(
            cid=conversation_id or uuid.uuid4().hex, mode=mode, spec=spec, question=question,
            user_question=question, collection=collection, scope=scope, top_k=top_k,
            answer_language=answer_language, external=external,
            use_english_retrieval=use_english_retrieval, use_context=use_context,
            candidate_k=candidate_k, use_reranker=use_reranker, answer_scope=answer_scope,
            started=time.monotonic(), fulltext_doc=fulltext_doc,
            fulltext_content=fulltext_content, retrieval_question=question,
            chat_meta={"scope": answer_scope},
        )

    def _ask_progress(self, req: AskRequest) -> Progress:
        def progress(stage: str, pct: int, detail: dict | None = None) -> None:
            if self._progress_store is not None:
                self._progress_store.set(req.cid, stage, pct, detail)
            self._runtime_events.progress(
                operation_id=req.cid,
                category="llm",
                operation="ask",
                phase=stage,
                percent=pct,
                msg=f"Ask stage: {stage}",
                metadata={
                    "conversation_id": req.cid,
                    "collection": req.collection or "all",
                    "mode": req.mode,
                    **(detail or {}),
                },
            )

        return progress

    # ── 准备 ────────────────────────────────────────────────

    async def _ask_prepare(self, req: AskRequest) -> None:
        """追问改写与检索词翻译。两者都调 LLM、都可能卡住，所以与后面的阶段同在一个可被
        cancel_ask() 中断的任务里。"""
        # 多轮对话：追问先改写成独立问题，检索与生成都用它；聊天记录保存用户原话。
        if req.use_context:
            req.standalone_question = await self._standalone_question(  # type: ignore[attr-defined]
                req.cid, req.question, req.answer_language
            )
        if req.standalone_question:
            req.question = req.standalone_question
            # 存进聊天记录：超时补拉与重启后读历史时恢复「理解为」。
            req.chat_meta["standalone_question"] = req.standalone_question

        # 英文检索：只有含中文才翻译（英文 query 不白耗一次 LLM 调用）；不做召回的模式不译。
        req.retrieval_question = req.question
        if (
            req.use_english_retrieval
            and req.spec.translates_query
            and self._llm_adapter is not None
            and uses_chinese(req.question)
        ):
            try:
                prompt = (
                    "Translate the following query to English for document retrieval."
                    " Output only the English translation, nothing else:\n\n"
                    f"{req.question}"
                )
                translated = await self._llm_adapter.generate(
                    prompt,
                    system_prompt=(
                        "You are a translation assistant."
                        " Output only the English translation, concisely."
                    ),
                    allow_mock=False,
                )
                if translated and translated.strip():
                    req.retrieval_question = translated.strip()
                    logger.info(
                        "Query translated for retrieval: %r → %r",
                        req.question,
                        req.retrieval_question,
                    )
            except Exception as exc:
                logger.warning("Query translation failed, using original: %s", exc)

    # ── 取证据 → 补充证据 → 合成 → 收尾 ─────────────────────

    async def _ask_answer(self, req: AskRequest, progress: Progress) -> dict:
        evidence: Evidence = await self._gather(req, progress)  # type: ignore[attr-defined]
        session, summary = await self._augment(req, evidence, progress)
        draft: Draft = await self._synthesize(  # type: ignore[attr-defined]
            req, evidence, summary, progress
        )
        return await self._finalize(req, evidence, draft, session, summary, progress)

    async def _augment(
        self, req: AskRequest, evidence: Evidence, progress: Progress
    ) -> tuple[Any, dict[str, Any] | None]:
        """补充证据（联网补充）。开关关着什么都不做（与 2.2.0 一致）；模式不支持时如实说明。"""
        if not req.external:
            return None, None
        if not req.spec.supports_external:
            return None, {"status": "unavailable", "reason": "mode"}
        return await self._collect_external(
            req.question, req.cid, evidence.sources, evidence.chunks, evidence.context_parts,
            progress,
        )

    async def _collect_external(
        self,
        question: str,
        conv_id: str,
        sources: list[dict[str, Any]],
        chunks: list[Any],
        context_parts: list[str],
        progress: Progress,
    ) -> tuple[Any, dict[str, Any]]:
        """联网补充：distributor 挑工具调用并登记引用，通过核验的引用就地追加到 sources / chunks /
        context_parts（编号续接）。失败不影响回答：返回 (None, 摘要)，status 为
        unavailable / error。"""
        from kacore.api_external import external_chunk, external_source_entry
        from kacore.managers.library_identity import LibraryIdentity
        from kacore.managers.tool_distributor import ExternalUnavailable

        if self._tool_distributor is None:
            return None, {"status": "unavailable", "reason": "no_tools",
                          "error": "external tools are not configured"}
        progress("external", 70)
        excerpts = [(str(s.get("title") or ""), str(s.get("text") or "")) for s in sources[:8]]
        try:
            session = await self._tool_distributor.run(
                question, excerpts, conv_id=conv_id, progress=lambda stage: progress(stage, 72)
            )
        except ExternalUnavailable as exc:
            return None, {"status": "unavailable", "reason": exc.reason, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 联网补充失败时照常用库内证据回答
            logger.warning("External tools failed, answering from the library only: %s", exc)
            return None, {"status": "error", "error": str(exc)}
        # 已在库的文献（DOI 或 ISBN 对上）不作外部证据：库内全文更可靠，检索时已能召回。
        in_library = await LibraryIdentity(self._source_store).table()
        used = 0
        for citation in session.citations:
            if LibraryIdentity.contains(in_library, str(citation.fields.get("identifier") or "")):
                continue
            n = len(sources) + 1
            entry = external_source_entry(session, citation, n)
            sources.append(entry)
            chunks.append(external_chunk(citation))
            where = f" (Page {citation.page})" if citation.page is not None else ""
            context_parts.append(f"[{n}] {entry['title']}{where} [external]\n{citation.passage}")
            used += 1
        return session, {
            "status": "ok",
            "capture_id": session.capture.capture_id,
            "calls": sum(1 for c in session.calls.values() if c.tool != "cite"),
            "registered": len(session.citations),
            "rejected": session.rejected,
            "used": used,
            "in_library": len(session.citations) - used,
            "cited": 0,
        }

    async def _finish_external(self, cid: str, session: Any, cited: set[int]) -> None:
        """回答写进聊天记录后：在账本里写上回答序号并标出正文用到的引用。失败只记日志。"""
        try:
            msg_idx = len(await self._source_store.get_chat_messages(cid)) - 1
            await self._source_store.finish_capture(
                session.capture.capture_id, max(msg_idx, 0), cited
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to finish external capture for %s: %s", cid, exc)

    async def _finalize(
        self,
        req: AskRequest,
        ev: Evidence,
        draft: Draft,
        session: Any,
        summary: dict[str, Any] | None,
        progress: Progress,
    ) -> dict:
        """收尾：每个模式只写这一处。引用改写、提示、存库、账本、运行事件、返回结果。"""
        answer, sources = draft.answer, ev.sources
        # 把 LLM 写的 [n] 确定性改写为 Harvard in-text 短引（带页码）并追加参考文献表。这里是唯一
        # 应用点——answer 已定稿，sources 就地补上 harvard_* 字段供前端还原点击态。引用由代码而非
        # 模型生成，杜绝编造作者与年份。
        cited_external: set[int] = set()
        if session is not None:        # 联网补充没跑时这里什么都不做（与 2.2.0 一致）
            cited_external = cited_external_ords(answer if draft.citable else "", sources)
            if summary is not None:
                summary["cited"] = len(cited_external)
        references: list[str] = []
        if draft.citable and sources:
            answer, references = render_answer_with_references(
                answer, sources, heading=reference_heading(req.answer_language, req.question)
            )
        else:
            annotate_sources(sources)

        # 告警是结构化 answer_notice 字段，不拼进正文开头；叠加生成状态（截断 / 过滤 …）提示。
        answer_notice = (
            compute_answer_notice(
                ev.deep_outcome, req.answer_language, req.question, draft.generation_status
            )
            if answer
            else ""
        )

        progress("done", 100)
        engines = list(dict.fromkeys(ev.engines))
        # 一次 ask 的收尾摘要：检索/生成各花多久、命中多少、走了哪些引擎。
        # 排查「问答变慢/超时」时，这一行就能区分是检索慢还是 LLM 慢。
        now = time.monotonic()
        t_llm = draft.llm_started or now
        logger.info(
            "ask done mode=%s engine=%s hits=%d "
            "retrieval %.1fs + generation %.1fs = total %.1fs",
            req.mode,
            ",".join(engines) or "none",
            len(sources),
            t_llm - req.started,
            now - t_llm,
            now - req.started,
        )
        if ev.fallback_reason:
            logger.warning("ask fell back in a controlled way: %s", ev.fallback_reason)

        # notice 以分隔线**前置**到存库答案：历史回放自包含不丢提示，且告警先于正文被看到。
        ledger = current_usage.get()
        if ev.deep_outcome is not None and ledger is not None:
            ev.deep_outcome.evidence_trace["usage"] = ledger.summary()
            if sources:
                sources[0]["request_trace"] = ev.deep_outcome.evidence_trace
        stored_answer = f"{answer_notice}\n\n---\n\n{answer}" if answer_notice else answer
        thinking_trace = serialize_deep_thinking(ev.deep_outcome) if ev.deep_outcome else None
        try:
            await self._source_store.add_chat_message(req.cid, "user", req.user_question)
            await self._source_store.add_chat_message(
                req.cid, "assistant", stored_answer,
                sources=list(sources),
                retrieval_mode=ev.stored_mode,
                meta={
                    **req.chat_meta,
                    **({"thinking_trace": thinking_trace} if thinking_trace else {}),
                    **({"external": summary} if summary else {}),
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist chat history: %s", exc)
        if session is not None:
            await self._finish_external(req.cid, session, cited_external)

        self._runtime_events.finish(
            operation_id=req.cid,
            category="llm",
            operation="ask",
            msg="Ask finished",
            status="warning" if ev.fallback_reason else "ok",
            level="WARNING" if ev.fallback_reason else "INFO",
            metadata={
                "conversation_id": req.cid,
                "mode": req.mode,
                "engines": ",".join(engines) or "none",
                "sources": len(sources),
                "fallback_reason": ev.fallback_reason or "",
                **ev.event_meta,
            },
        )
        return {
            "conversation_id": req.cid,
            "answer": answer,
            "answer_notice": answer_notice,
            "references": references,
            "sources": sources,
            "requested_retrieval_mode": req.mode,
            "actual_retrieval_mode": ev.stored_mode,
            "retrieval_engines": engines,
            "fallback_reason": ev.fallback_reason,
            "thinking_trace": thinking_trace,
            # 生成状态：length / content_filter / reasoning_only / llm_unavailable，正常为空。
            "generation_status": draft.generation_status,
            # 只有打开联网补充的提问才带这个键；关着时返回的形状与 2.2.0 完全一样。
            **({"external": summary} if req.external else {}),
        }


__all__ = ["AnswerPromptMixin", "AskApiMixin"]
