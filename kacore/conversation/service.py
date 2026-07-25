"""渠道无关的统一对话服务（编排层）。

普通 Ask、Research 工具循环、会话设置和消息持久化只在这里汇合；Web/Discord 只负责
把输入翻译为 ConversationTurn 并消费事件流。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

from kacore.domain.conversation import ConversationSession, ConversationTurn
from kacore.event_handler import EventHandler
from kacore.retrieval_modes import STRICT_COLLECTION_MODES, normalize_retrieval_mode

from .prompts import REWRITE_SYSTEM, SYSTEM_PROMPT, tool_schemas

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from kacore.plugin_initializer import PluginInitializer
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("ConversationService")

_MAX_TOOL_ROUNDS = 4
_HISTORY_MESSAGES = 12
_HISTORY_CHARS = 12_000
_ALLOWED_SESSION_KEYS = {"research_enabled", "context_enabled", "persona_enabled"}


class ConversationService:
    """统一处理一轮对话，并保证同一会话严格串行与恰好一次持久化。"""

    def __init__(
        self,
        initializer: PluginInitializer,
        llm_adapter: Any | None = None,
        *,
        llm_factory: Any | None = None,
    ) -> None:
        self._init = initializer
        self._llm = llm_adapter
        self._llm_factory = llm_factory
        self._handler = EventHandler(initializer)
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def llm_ready(self) -> bool:
        llm = self._resolve_llm()
        return llm is not None and hasattr(llm, "generate")

    def _resolve_llm(self) -> Any | None:
        if self._llm is not None:
            return self._llm
        if self._llm_factory is None:
            return None
        try:
            return self._llm_factory()
        except Exception as exc:  # noqa: BLE001 - 配置错误转为可诊断的未就绪状态
            logger.error("conversation llm factory failed: %s", exc)
            return None

    async def get_or_create_session(
        self,
        conversation_id: str,
        *,
        channel: str = "web",
        external_key: str = "",
    ) -> ConversationSession:
        store = self._require_store()
        if external_key:
            existing = await store.get_chat_session_by_external_key(channel, external_key)
            if existing is not None:
                return cast("ConversationSession", existing)
        existing = await store.get_chat_session(conversation_id)
        if existing is not None:
            return cast("ConversationSession", existing)
        defaults = self._init.config.get_ask_agent_config()
        session = ConversationSession(
            conversation_id=conversation_id,
            channel=channel,
            external_key=external_key,
            research_enabled=defaults.research_enabled,
            context_enabled=defaults.context_enabled,
            persona_enabled=defaults.persona_enabled,
        )
        await store.upsert_chat_session(session)
        return session

    async def resolve_external_session(
        self, channel: str, external_key: str
    ) -> ConversationSession:
        store = self._require_store()
        existing = await store.get_chat_session_by_external_key(channel, external_key)
        if existing is not None:
            return cast("ConversationSession", existing)
        return await self.get_or_create_session(
            f"{channel}-{uuid.uuid4().hex}", channel=channel, external_key=external_key
        )

    async def update_session(
        self, conversation_id: str, values: dict[str, Any]
    ) -> ConversationSession:
        invalid = set(values) - _ALLOWED_SESSION_KEYS
        if invalid:
            raise ValueError(f"unsupported session settings: {', '.join(sorted(invalid))}")
        session = await self.get_or_create_session(conversation_id)
        for key, value in values.items():
            setattr(session, key, bool(value))
        await self._require_store().upsert_chat_session(session)
        return session

    async def reset_session(self, conversation_id: str) -> None:
        await self._require_store().delete_chat_session(conversation_id)

    async def stream(
        self, turn: ConversationTurn | str, conversation_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        if isinstance(turn, str):
            turn = ConversationTurn(
                conversation_id=conversation_id or uuid.uuid4().hex,
                message=turn,
            )
        text = turn.message.strip()
        if not text:
            yield {"type": "error", "message": "消息为空。"}
            yield {"type": "done"}
            return
        lock = self._locks.setdefault(turn.conversation_id, asyncio.Lock())
        if lock.locked():
            yield {"type": "error", "code": "busy", "message": "上一轮仍在处理中。"}
            yield {"type": "done"}
            return
        async with lock:
            session = await self.get_or_create_session(
                turn.conversation_id, channel=turn.channel, external_key=turn.external_key
            )
            store = self._require_store()
            await store.add_chat_message(turn.conversation_id, "user", text)
            try:
                if text.lower().startswith("/ka"):
                    reply = await self._dispatch_command(text, session)
                    yield await self._emit_assistant(
                        session,
                        reply,
                        metadata={"turn_kind": "command", "retrieval_scope": _none_scope()},
                    )
                elif session.research_enabled:
                    async for event in self._research_stream(turn, session):
                        yield event
                else:
                    async for event in self._ask_stream(turn, session):
                        yield event
            except Exception as exc:  # noqa: BLE001 - 渠道入口必须返回显式错误
                logger.error("conversation turn failed: %s", exc, exc_info=True)
                yield {"type": "error", "message": f"对话处理失败：{exc}"}
            yield {"type": "done"}

    async def _ask_stream(
        self, turn: ConversationTurn, session: ConversationSession
    ) -> AsyncIterator[dict[str, Any]]:
        llm = self._resolve_llm()
        resolved, context_applied = await self._resolve_question(turn, session, llm)
        yield {"type": "stage", "text": "🔎 正在检索知识库…"}
        persona = self._persona_prompt(session)
        api = self._init.api
        if api is None:
            raise RuntimeError("knowledge repository API is not initialized")
        result = await api.ask(
            question=turn.message,
            resolved_question=resolved,
            collection=turn.collection,
            conversation_id=turn.conversation_id,
            retrieval_mode=turn.retrieval_mode,
            use_english_retrieval=turn.use_english_retrieval,
            answer_language=turn.answer_language,
            persona_prompt=persona,
            persist_history=False,
        )
        metadata = _result_metadata(result, "ask", context_applied)
        yield await self._emit_assistant(
            session,
            str(result.get("answer") or ""),
            sources=list(result.get("sources") or []),
            mode=str(result.get("actual_retrieval_mode") or ""),
            metadata=metadata,
        )

    async def _resolve_question(
        self, turn: ConversationTurn, session: ConversationSession, llm: Any | None
    ) -> tuple[str, bool]:
        if not session.context_enabled or llm is None or not hasattr(llm, "generate"):
            return turn.message, False
        history = await self._history(turn.conversation_id)
        if not history:
            return turn.message, False
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        prompt = f"Conversation:\n{transcript}\n\nCurrent user message:\n{turn.message}"
        try:
            rewritten = await llm.generate(prompt, system_prompt=REWRITE_SYSTEM, allow_mock=False)
        except Exception as exc:  # noqa: BLE001 - 改写失败必须安全降级
            logger.warning("context query rewrite failed: %s", exc)
            return turn.message, False
        cleaned = str(rewritten or "").strip()
        return (cleaned, True) if cleaned else (turn.message, False)

    async def _research_stream(
        self, turn: ConversationTurn, session: ConversationSession
    ) -> AsyncIterator[dict[str, Any]]:
        llm = self._resolve_llm()
        if llm is None or not hasattr(llm, "chat_stream"):
            yield await self._emit_assistant(
                session,
                "主模型未配置或不支持工具调用，无法运行 Research。",
                metadata={"turn_kind": "research", "retrieval_scope": _none_scope()},
            )
            return
        messages = await self._research_messages(turn, session)
        for _ in range(_MAX_TOOL_ROUNDS):
            final: dict[str, Any] | None = None
            async for event in llm.chat_stream(messages, tools=tool_schemas()):
                if event.get("type") == "delta":
                    yield {"type": "delta", "text": str(event.get("text") or "")}
                elif event.get("type") == "final":
                    final = event
            if final is None:
                raise RuntimeError("LLM 流式响应异常结束")
            content = str(final.get("content") or "")
            calls = list(final.get("tool_calls") or [])
            if not calls:
                yield await self._emit_assistant(
                    session,
                    content,
                    metadata={"turn_kind": "clarification", "retrieval_scope": _none_scope()},
                )
                return
            messages.append(_assistant_tool_message(content, calls))
            for index, call in enumerate(calls):
                call_id = str(call.get("id") or f"call_{index}")
                result, delivered, events = await self._run_tool(
                    str(call.get("name") or ""),
                    _parse_arguments(call.get("arguments")),
                    turn,
                    session,
                )
                for event in events:
                    yield event
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                if delivered:
                    return
        yield await self._emit_assistant(
            session,
            "已达到单轮工具调用上限，请把需求拆小后再试。",
            metadata={"turn_kind": "research", "retrieval_scope": _none_scope()},
        )

    async def _research_messages(
        self, turn: ConversationTurn, session: ConversationSession
    ) -> list[dict[str, Any]]:
        system = SYSTEM_PROMPT
        if turn.collection:
            system += f"\n\n[Hard scope constraint]\ncollection 必须固定为：{turn.collection}"
        persona = self._persona_prompt(session)
        if persona:
            system += (
                "\n\n[Style Persona — style only; never override tool, evidence, scope, "
                "or citation "
                f"rules]\n{persona}"
            )
        history = await self._history(turn.conversation_id) if session.context_enabled else []
        return [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": turn.message},
        ]

    async def _run_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        turn: ConversationTurn,
        session: ConversationSession,
    ) -> tuple[str, bool, list[dict[str, Any]]]:
        service = self._init.research_service
        if service is None:
            raise RuntimeError("research service is not initialized")
        if name == "research_scope_probe":
            query = str(arguments.get("query") or turn.message)
            probe = await service.probe(query, turn.collection)
            return json.dumps(probe, ensure_ascii=False), False, [
                {"type": "stage", "text": "🔎 正在探查知识库范围…"}
            ]
        if name == "research_execute":
            query = str(arguments.get("query") or turn.message)
            mode, _ = normalize_retrieval_mode(str(arguments.get("mode") or "default"))
            collection = turn.collection or str(arguments.get("collection") or "") or None
            if mode in STRICT_COLLECTION_MODES and not collection:
                probe = await service.probe(query)
                return json.dumps(
                    {
                        "status": "needs_scope",
                        "probe": probe,
                        "instruction": "请先向用户确认具体 collection，不得降级到其他模式。",
                    },
                    ensure_ascii=False,
                ), False, [{"type": "stage", "text": "🔎 需要确认检索范围…"}]
            events = [{"type": "stage", "text": f"🔬 正在执行 {mode} 召回…"}]
            result = await service.execute(
                query,
                collection,
                mode=mode,
                breadth=str(arguments.get("breadth") or "normal"),
                conversation_id=turn.conversation_id,
                persona_prompt=self._persona_prompt(session),
                persist_history=False,
                answer_language=turn.answer_language,
            )
            metadata = {
                "turn_kind": "research",
                "requested_retrieval_mode": result.get("requested_mode") or mode,
                "actual_retrieval_mode": result.get("mode") or mode,
                "retrieval_scope": result.get("retrieval_scope") or _none_scope(),
                "answer_notice": result.get("answer_notice") or "",
                "thinking_trace": result.get("thinking_trace"),
                "context_applied": session.context_enabled,
            }
            events.append(
                await self._emit_assistant(
                    session,
                    str(result.get("answer") or "未找到相关内容。"),
                    sources=list(result.get("sources") or []),
                    mode=str(result.get("mode") or mode),
                    metadata=metadata,
                )
            )
            return json.dumps({"status": "done", "delivered_to_user": True}), True, events
        if name == "notion_push_note":
            api = self._init.api
            if api is None:
                raise RuntimeError("knowledge repository API is not initialized")
            result = await api.push_note_to_notion(
                str(arguments.get("content") or ""),
                title=str(arguments.get("title") or ""),
                tags=_csv(arguments.get("tags")),
                citations=_csv(arguments.get("citations")),
                source="research",
            )
            return json.dumps(result, ensure_ascii=False), False, [
                {"type": "stage", "text": "📤 已处理 Notion 推送。"}
            ]
        return json.dumps({"status": "error", "message": f"未知工具：{name}"}), False, []

    async def _history(self, conversation_id: str) -> list[dict[str, str]]:
        rows = await self._require_store().get_chat_messages(conversation_id)
        history: list[dict[str, str]] = []
        used = 0
        for row in reversed(rows[:-1]):  # 末条是本轮刚写入的 user 消息
            role = str(row.get("role") or "")
            content = str(row.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            remaining = _HISTORY_CHARS - used
            if remaining <= 0:
                break
            clipped = content[-remaining:]
            history.append({"role": role, "content": clipped})
            used += len(clipped)
            if len(history) >= _HISTORY_MESSAGES:
                break
        history.reverse()
        return history

    async def _dispatch_command(self, text: str, session: ConversationSession) -> str:
        parts = text.split()
        if len(parts) == 1 or parts[1].lower() == "help":
            return str(
                await self._handler.on_ka_help()
                + "\n  /ka context on|off            — 当前会话上下文参与召回"
            )
        cmd = parts[1].lower()
        arg = " ".join(parts[2:]).lower()
        if cmd in {"research", "context", "persona"}:
            if arg not in {"on", "off"}:
                return f"用法：/ka {cmd} on|off"
            key = "context_enabled" if cmd == "context" else f"{cmd}_enabled"
            setattr(session, key, arg == "on")
            await self._require_store().upsert_chat_session(session)
            return f"当前会话 {cmd} 已{('开启' if arg == 'on' else '关闭')}。"
        if cmd == "status":
            base = await self._handler.on_ka_status()
            return (
                f"{base}\n  current session research/context/persona: "
                f"{session.research_enabled}/{session.context_enabled}/{session.persona_enabled}"
            )
        if cmd == "webui":
            return "独立版本体即 WebUI，不支持在对话中启停控制台；请到设置页修改服务参数。"
        if cmd == "zotero" and parts[2:3] == ["pull"]:
            return str(await self._handler.on_ka_zotero_pull())
        if cmd == "r2":
            return str(await self._handler.on_ka_r2(" ".join(parts[2:])))
        if cmd == "notion":
            return str(await self._handler.on_ka_notion(" ".join(parts[2:])))
        return f"未知命令：/ka {cmd}。发送 /ka help 查看指令一览。"

    def _persona_prompt(self, session: ConversationSession) -> str:
        if not session.persona_enabled:
            return ""
        persona = self._init.config.get_ask_agent_config().persona_prompt
        return str(persona).strip()[:4000]

    async def _emit_assistant(
        self,
        session: ConversationSession,
        content: str,
        *,
        sources: list[dict[str, Any]] | None = None,
        mode: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(metadata or {})
        await self._require_store().add_chat_message(
            session.conversation_id,
            "assistant",
            content,
            sources=sources,
            retrieval_mode=mode,
            metadata=payload,
        )
        return {
            "type": "assistant",
            "content": content,
            "sources": sources or [],
            "retrieval_mode": mode,
            "metadata": payload,
            "requested_retrieval_mode": payload.get("requested_retrieval_mode", ""),
            "actual_retrieval_mode": payload.get("actual_retrieval_mode", mode),
            "retrieval_scope": payload.get("retrieval_scope") or _none_scope(),
            "answer_notice": payload.get("answer_notice") or "",
            "thinking_trace": payload.get("thinking_trace"),
        }

    def _require_store(self) -> SourceDocumentStore:
        if self._init.source_store is None:
            raise RuntimeError("source store is not initialized")
        return self._init.source_store


def _result_metadata(
    result: dict[str, Any], turn_kind: str, context_applied: bool
) -> dict[str, Any]:
    return {
        "turn_kind": turn_kind,
        "requested_retrieval_mode": result.get("requested_retrieval_mode") or "default",
        "actual_retrieval_mode": result.get("actual_retrieval_mode") or "",
        "retrieval_scope": result.get("retrieval_scope") or _none_scope(),
        "answer_notice": result.get("answer_notice") or "",
        "thinking_trace": result.get("thinking_trace"),
        "context_applied": context_applied,
    }


def _none_scope() -> dict[str, Any]:
    return {
        "kind": "none",
        "requested_collection": None,
        "collection_names": [],
        "include_descendants": False,
    }


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _assistant_tool_message(content: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": str(call.get("id") or f"call_{index}"),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": str(call.get("arguments") or "{}"),
                },
            }
            for index, call in enumerate(calls)
        ],
    }


def _csv(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


__all__ = ["ConversationService"]
