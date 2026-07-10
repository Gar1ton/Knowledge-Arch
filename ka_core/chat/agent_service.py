"""对话 Agent 服务：复现 AstrBot 下的使用模式（见 apps/plugin/main.py 的注册面）。

三条通路，全部委派既有子系统、不新造检索/同步逻辑：
1. ``/ka`` 命令 —— 与插件同一套 ``EventHandler`` 命令体（help/status/开关/zotero/r2/notion）。
2. agent 上下文注入 —— ``agent_enabled`` 开启时，把知识库召回块注入 system prompt
   （与 ``EventHandler.on_llm_request`` 同一取数口径）。
3. 工具循环 —— research_scope_probe / research_execute / notion_push_note 三工具经
   OpenAI tool-calling 暴露给主 LLM；research 结果由本服务直接推送给用户
   （工具回执告知模型「已送达，勿复述」，与插件的后台回发模型一致）。

事件流协议（SSE 载荷，由 ka_web 原样转发）：
  {"type": "stage", "text"}                 阶段提示（工具执行中）
  {"type": "delta", "text"}                 模型正文增量
  {"type": "assistant", "content", ...}     一条完整助手消息（已持久化）
  {"type": "error", "message"}              显式错误
  {"type": "done"}                          本轮结束
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from ka_core.event_handler import EventHandler
from ka_core.retrieval_modes import (
    MODE_GRAPH_MIXED,
    MODE_GRAPH_ONLY,
    STRICT_COLLECTION_MODES,
    normalize_retrieval_mode,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ka_core.plugin_initializer import PluginInitializer

logger = logging.getLogger("ChatAgentService")

_MAX_TOOL_ROUNDS = 4
_HISTORY_WINDOW = 12
_RAG_TOP_K = 3

_SYSTEM_PROMPT = (
    "你是 Knowledge Arch 知识库的对话助手。用户在独立应用的对话界面与你交流，"
    "语气自然、简洁、准确。当用户问到已收藏文献/研究内容时，先调用 research_scope_probe "
    "了解范围：ambiguity=low 可直接 research_execute 并说明所用范围；medium/high 则先用"
    "自然语言把候选范围与建议模式告诉用户、确认后再执行。research_execute 的结果会由系统"
    "直接展示给用户，你只需简短确认或补充，不要复述全文。用户要求把结论保存/推送到 Notion "
    "时调用 notion_push_note（content 传完整正文原文）。工具均只读（notion_push_note 仅新增"
    "问答记录），绝不修改任何同步配置。运营控制命令请提示用户直接输入 /ka 指令"
    "（如 /ka help、/ka status）。"
)

_WEBUI_GUARD_TEXT = "独立版本体即 WebUI，不支持在对话中启停控制台；如需更改服务参数请到设置页。"


def _tool_schemas() -> list[dict[str, Any]]:
    """OpenAI tool-calling 形状的三工具 schema（语义与 apps/plugin/main.py 对齐）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "research_scope_probe",
                "description": (
                    "探查知识库里与问题相关的范围：命中的论文/集合/标签、ambiguity"
                    "（low/medium/high）、建议召回模式与可用模式。用户问到文献/研究内容时先调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "用户的完整问题（结合上下文改写后的检索意图）。",
                        }
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "research_execute",
                "description": (
                    "在范围明确或用户确认后执行知识库召回并作答；结果由系统直接展示给用户。"
                    "mode 成本阶梯：default（查存/单点事实）< enhanced（分析/对比/机制）"
                    "< deep_thinking（仅综述级，需绑定 collection）；graph_mixed/graph_only "
                    "需 LightRAG 就绪。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "凝练后的自包含检索指令（不要原样转发零碎对话）。",
                        },
                        "collection": {
                            "type": "string",
                            "description": "召回范围集合名；留空=全局检索。",
                        },
                        "mode": {
                            "type": "string",
                            "enum": [
                                "default",
                                "enhanced",
                                "deep_thinking",
                                "graph_mixed",
                                "graph_only",
                            ],
                        },
                        "breadth": {"type": "string", "enum": ["narrow", "normal", "wide"]},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "notion_push_note",
                "description": (
                    "把一段研究结论或笔记推送到 Notion 问答库（每次新增一条记录）。"
                    "content 必须传完整正文原文；citations 传 research 返回的 doc_id 逗号分隔。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "title": {"type": "string"},
                        "tags": {"type": "string", "description": "逗号分隔标签，可空。"},
                        "citations": {"type": "string", "description": "逗号分隔 doc_id，可空。"},
                        "keep_local": {"type": "string", "enum": ["true", "false"]},
                    },
                    "required": ["content"],
                },
            },
        },
    ]


_LLM_NOT_CONFIGURED_TEXT = (
    "主模型尚未配置，暂时无法进行自然语言对话。\n\n"
    "请点击右上角「控制台 →」，在 设置 → 主模型（OpenAI 兼容） 填写端点 base_url 与模型名"
    "（API Key 经环境变量 KR_MAIN_LLM_API_KEY 注入），保存后回到本页即可对话，无需重启。\n"
    "（/ka 运营命令不依赖主模型，现在就可以使用，例如 /ka status。）"
)


class ChatAgentService:
    """独立版对话 Agent：命令分发 + 注入 + 工具循环（构造器注入，自身无状态存储）。

    主 LLM 二选一注入：``llm_adapter`` 固定适配器（测试/插件场景），或 ``llm_factory``
    惰性工厂（独立壳——每轮对话按最新持久化配置解析，设置页保存 main_llm 后即刻生效）。
    /ka 命令通路不依赖主 LLM，未配置时仍可用。
    """

    def __init__(
        self,
        initializer: PluginInitializer,
        llm_adapter: Any | None = None,
        *,
        llm_factory: Any | None = None,
    ) -> None:
        self._init = initializer
        self._llm = llm_adapter  # 须支持 chat_stream(messages, tools=...)
        self._llm_factory = llm_factory
        self._handler = EventHandler(initializer)

    def _resolve_llm(self) -> Any | None:
        if self._llm is not None:
            return self._llm
        if self._llm_factory is not None:
            try:
                return self._llm_factory()
            except Exception as exc:  # noqa: BLE001 - 工厂失败按未配置处理并记日志
                logger.error("chat agent llm factory failed: %s", exc)
        return None

    @property
    def llm_ready(self) -> bool:
        """主 LLM 是否就绪（决定自然语言通路可用性；/ka 命令不受影响）。"""
        adapter = self._resolve_llm()
        return adapter is not None and hasattr(adapter, "chat_stream")

    # ── 入口 ─────────────────────────────────────────────────────

    async def stream(self, message: str, conversation_id: str) -> AsyncIterator[dict[str, Any]]:
        """处理一条用户输入，产出事件流；用户与最终助手消息均持久化到 chat_history。"""
        store = self._init.source_store
        text = (message or "").strip()
        if not text:
            yield {"type": "error", "message": "消息为空。"}
            yield {"type": "done"}
            return
        if store is not None:
            await store.add_chat_message(conversation_id, "user", text)

        try:
            if text.startswith("/ka") or text.startswith("/KA"):
                reply = await self._dispatch_command(text)
                yield await self._emit_assistant(conversation_id, reply, mode="command")
            else:
                async for event in self._agent_stream(text, conversation_id):
                    yield event
        except Exception as exc:  # noqa: BLE001 - 对话入口兜底，错误显式回给用户
            logger.error("chat agent stream failed: %s", exc, exc_info=True)
            yield {"type": "error", "message": f"对话处理失败：{exc}"}
        yield {"type": "done"}

    # ── /ka 命令分发（复用 EventHandler 命令体）───────────────────

    async def _dispatch_command(self, text: str) -> str:
        parts = text.split()
        if len(parts) == 1:
            return await self._handler.on_ka_help()
        cmd, rest = parts[1].lower(), parts[2:]
        arg = " ".join(rest)
        if cmd == "help":
            return await self._handler.on_ka_help()
        if cmd == "status":
            return await self._handler.on_ka_status()
        if cmd == "agent":
            return await self._handler.on_ka_agent(arg)
        if cmd == "research":
            return await self._handler.on_ka_research(arg)
        if cmd == "research_language":
            return await self._handler.on_ka_research_language(arg)
        if cmd == "persona":
            return await self._handler.on_ka_persona(arg)
        if cmd == "webui":
            return _WEBUI_GUARD_TEXT
        if cmd == "zotero":
            if rest and rest[0].lower() == "pull":
                return await self._handler.on_ka_zotero_pull()
            if rest and rest[0].lower() == "account":
                return await self._handler.on_ka_zotero_account(
                    rest[1] if len(rest) > 1 else ""
                )
            return "用法：/ka zotero pull | /ka zotero account <replace|cancel>"
        if cmd == "r2":
            reply = await self._handler.on_ka_r2(arg)
            if "任务已启动" in reply:
                reply += "\n（进度可在控制台「同步/配额」页查看。）"
            return reply
        if cmd == "notion":
            reply = await self._handler.on_ka_notion(arg)
            if "任务已启动" in reply:
                asyncio.create_task(self._run_notion_push(force="全量" in reply))
                reply += "\n（推送在后台执行，进度可在控制台「同步」页查看。）"
            return reply
        return f"未知命令：/ka {cmd}。发送 /ka help 查看指令一览。"

    async def _run_notion_push(self, force: bool) -> None:
        """/ka notion push 的后台执行（对齐插件 main.py 的 watch 语义，结果记日志）。"""
        api = self._init.api
        if api is None:
            return
        try:
            await api.sync_documents("notion", force=force)
        except Exception as exc:  # noqa: BLE001 - 后台任务不外抛
            logger.warning("chat-triggered notion push failed: %s", exc)

    # ── agent 通路（注入 + 工具循环）─────────────────────────────

    async def _agent_stream(
        self, text: str, conversation_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        llm = self._resolve_llm()
        if llm is None or not hasattr(llm, "chat_stream"):
            yield await self._emit_assistant(
                conversation_id, _LLM_NOT_CONFIGURED_TEXT, mode="agent"
            )
            return

        messages = await self._build_messages(text, conversation_id)
        tools = _tool_schemas()

        for _ in range(_MAX_TOOL_ROUNDS):
            final: dict[str, Any] | None = None
            async for event in llm.chat_stream(messages, tools=tools):
                if event.get("type") == "delta":
                    yield {"type": "delta", "text": event.get("text", "")}
                elif event.get("type") == "final":
                    final = event
            if final is None:
                raise RuntimeError("LLM 流式响应异常结束")

            content = str(final.get("content") or "")
            tool_calls = list(final.get("tool_calls") or [])
            if not tool_calls:
                yield await self._emit_assistant(conversation_id, content, mode="agent")
                return

            messages.append(_assistant_tool_message(content, tool_calls))
            for index, call in enumerate(tool_calls):
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or f"call_{index}")
                arguments = _parse_arguments(call.get("arguments"))
                async for event in self._run_tool(
                    name, arguments, conversation_id
                ):
                    if event.get("type") == "tool_result":
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": str(event.get("content") or ""),
                            }
                        )
                    else:
                        yield event

        yield await self._emit_assistant(
            conversation_id,
            "（已达到单轮工具调用上限，请把需求拆小后再试。）",
            mode="agent",
        )

    async def _build_messages(
        self, text: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        system = _SYSTEM_PROMPT
        rag_block = await self._rag_context(text)
        if rag_block:
            system += rag_block
        history: list[dict[str, Any]] = []
        store = self._init.source_store
        if store is not None:
            rows = await store.get_chat_messages(conversation_id)
            for row in rows[-(_HISTORY_WINDOW + 1) : -1]:  # 末条是刚存入的本次输入
                role = str(row.get("role") or "")
                content = str(row.get("content") or "")
                if role in ("user", "assistant") and content:
                    history.append({"role": role, "content": content})
        return [{"role": "system", "content": system}, *history, {"role": "user", "content": text}]

    async def _rag_context(self, query: str) -> str:
        """agent_enabled 时构造知识库上下文块（与 EventHandler.on_llm_request 同口径）。"""
        if not self._init.agent_enabled:
            return ""
        api = self._init.api
        orchestrator = self._init.retrieval_orchestrator
        if api is None or orchestrator is None or not query:
            return ""
        try:
            cols = await api.list_collections()
            names = [c.name for c in cols[:5]] if cols else ["default"]
            chunks: list[Any] = []
            seen: set[str] = set()
            for col in names:
                results = await orchestrator.retrieve(
                    collection=col, query=query, top_k=_RAG_TOP_K
                )
                for chunk in results:
                    if chunk.chunk_id not in seen:
                        seen.add(chunk.chunk_id)
                        chunks.append(chunk)
                    if len(chunks) >= _RAG_TOP_K:
                        break
                if len(chunks) >= _RAG_TOP_K:
                    break
            if not chunks:
                return ""
            lines = []
            for i, chunk in enumerate(chunks):
                doc = await api.get_document(chunk.doc_id)
                title = doc.title if doc else chunk.doc_id
                lines.append(f"[{i + 1}] 来源: 《{title}》 | 内容: {chunk.text}")
            return (
                "\n\n[Knowledge Base Context / 知识库召回]\n"
                "请优先结合以下从知识库召回的相关文献分块回答用户的问题，"
                "并以 [n] 格式标注引用来源：\n" + "\n".join(lines) + "\n"
            )
        except Exception as exc:  # noqa: BLE001 - 注入失败不阻断对话
            logger.error("chat agent rag inject failed: %s", exc)
            return ""

    # ── 工具执行 ─────────────────────────────────────────────────

    async def _run_tool(
        self, name: str, arguments: dict[str, Any], conversation_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """执行单个工具；yield stage/assistant 事件 + 最终 tool_result（回喂模型）。"""
        if name == "research_scope_probe":
            yield {"type": "stage", "text": "🔎 正在探查知识库范围…"}
            yield {"type": "tool_result", "content": await self._tool_probe(arguments)}
            return
        if name == "research_execute":
            async for event in self._tool_execute(arguments, conversation_id):
                yield event
            return
        if name == "notion_push_note":
            yield {"type": "stage", "text": "📤 正在推送到 Notion…"}
            yield {"type": "tool_result", "content": await self._tool_notion_push(arguments)}
            return
        yield {"type": "tool_result", "content": f"未知工具：{name}"}

    async def _tool_probe(self, arguments: dict[str, Any]) -> str:
        svc = self._init.research_service
        if svc is None or not self._init.research_enabled:
            return "research 未开启或未装配，请提示用户先发送 /ka research on。"
        try:
            probe = await svc.probe(str(arguments.get("query") or ""))
            return json.dumps(probe, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - 工具入口兜底
            logger.error("chat probe failed: %s", exc, exc_info=True)
            return json.dumps(
                {"status": "error", "message": f"范围探查失败：{exc}"}, ensure_ascii=False
            )

    async def _tool_execute(
        self, arguments: dict[str, Any], conversation_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        svc = self._init.research_service
        if svc is None or not self._init.research_enabled:
            yield {
                "type": "tool_result",
                "content": "research 未开启或未装配，请提示用户先发送 /ka research on。",
            }
            return

        query = str(arguments.get("query") or "")
        collection = str(arguments.get("collection") or "") or None
        mode, _ = normalize_retrieval_mode(str(arguments.get("mode") or "default"))
        breadth = str(arguments.get("breadth") or "normal") or "normal"

        if mode in STRICT_COLLECTION_MODES and not collection:
            try:
                probe = await svc.probe(query)
            except Exception as exc:  # noqa: BLE001
                yield {
                    "type": "tool_result",
                    "content": json.dumps(
                        {"status": "error", "message": f"范围探查失败：{exc}"}, ensure_ascii=False
                    ),
                }
                return
            collection = _collection_from_probe(probe)
            if collection is None:
                yield {
                    "type": "tool_result",
                    "content": json.dumps(
                        {
                            "status": "needs_scope",
                            "mode": mode,
                            "probe": probe,
                            "instruction": (
                                f"{mode} 必须绑定明确 collection。请向用户确认范围后，"
                                "带 collection 参数再次调用 research_execute；不要改用其它 mode。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
                return

        scope_label = collection or "全局"
        yield {"type": "stage", "text": _research_start_message(scope_label, mode, breadth)}
        try:
            result = await svc.execute(query, collection, mode=mode, breadth=breadth)
        except Exception as exc:  # noqa: BLE001
            logger.error("chat research_execute failed: %s", exc, exc_info=True)
            yield {
                "type": "tool_result",
                "content": json.dumps(
                    {"status": "error", "message": f"research_execute 执行失败：{exc}"},
                    ensure_ascii=False,
                ),
            }
            return

        formatted = _format_research_result(result)
        yield await self._emit_assistant(
            conversation_id,
            formatted,
            mode=str(result.get("mode") or mode),
            sources=list(result.get("sources") or []),
        )
        yield {
            "type": "tool_result",
            "content": json.dumps(
                {
                    "status": "done",
                    "delivered_to_user": True,
                    "scope": scope_label,
                    "mode": str(result.get("mode") or mode),
                    "citation_doc_ids": result.get("citation_doc_ids") or [],
                    "instruction": (
                        "结果已直接展示给用户，请简短确认或补充要点，不要复述全文；"
                        "不要重复调用 research_execute。"
                    ),
                },
                ensure_ascii=False,
            ),
        }

    async def _tool_notion_push(self, arguments: dict[str, Any]) -> str:
        api = self._init.api
        if api is None:
            return "插件未初始化，无法推送。"
        try:
            result = await api.push_note_to_notion(
                str(arguments.get("content") or ""),
                title=str(arguments.get("title") or ""),
                tags=[t.strip() for t in str(arguments.get("tags") or "").split(",") if t.strip()],
                citations=[
                    c.strip()
                    for c in str(arguments.get("citations") or "").split(",")
                    if c.strip()
                ],
                source="research",
                keep_local=str(arguments.get("keep_local") or "false").strip().lower()
                in ("true", "1", "yes"),
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("chat notion_push_note failed: %s", exc, exc_info=True)
            return json.dumps(
                {"status": "error", "message": f"推送到 Notion 失败：{exc}"}, ensure_ascii=False
            )

    # ── 输出与持久化 ─────────────────────────────────────────────

    async def _emit_assistant(
        self,
        conversation_id: str,
        content: str,
        *,
        mode: str = "",
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        store = self._init.source_store
        if store is not None and content:
            await store.add_chat_message(
                conversation_id,
                "assistant",
                content,
                sources=sources,
                retrieval_mode=mode,
            )
        return {
            "type": "assistant",
            "content": content,
            "retrieval_mode": mode,
            "sources": sources or [],
        }


# ── 纯函数助手（语义对齐 apps/plugin/main.py 的同名逻辑）────────


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _assistant_tool_message(
    content: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": str(call.get("id") or f"call_{i}"),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": str(call.get("arguments") or "{}"),
                },
            }
            for i, call in enumerate(tool_calls)
        ],
    }


def _collection_from_probe(probe: dict[str, Any]) -> str | None:
    if probe.get("ambiguity") != "low":
        return None
    collections = probe.get("collections")
    if not isinstance(collections, list) or not collections:
        return None
    first = collections[0]
    if not isinstance(first, dict):
        return None
    name = str(first.get("name") or "").strip()
    return name or None


def _research_start_message(scope: str, mode: str, breadth: str) -> str:
    if mode == "deep_thinking":
        return f"🔬 已开始 Deep Thinking：范围「{scope}」，breadth={breadth}，可能需要几分钟…"
    if mode == "enhanced":
        return f"🔍 已开始增强召回：范围「{scope}」，拆解检索 + 自检纠偏中…"
    if mode == MODE_GRAPH_MIXED:
        return f"🕸️ 已开始图谱混合检索：范围「{scope}」…"
    if mode == MODE_GRAPH_ONLY:
        return f"🕸️ 已开始纯图谱检索：范围「{scope}」…"
    return f"🔎 已开始检索：范围「{scope}」，mode={mode}，breadth={breadth}…"


def _format_research_result(result: dict[str, Any]) -> str:
    answer = str(result.get("answer") or "未找到相关内容。").strip()
    scope = str(result.get("scope") or "全局")
    mode = str(result.get("mode") or result.get("requested_mode") or "default")
    requested_mode = str(result.get("requested_mode") or mode)
    done_label = {
        "deep_thinking": "Deep Thinking 完成",
        "enhanced": "增强召回完成",
        MODE_GRAPH_MIXED: "图谱混合检索完成",
        MODE_GRAPH_ONLY: "纯图谱检索完成",
    }.get(requested_mode, "检索完成")
    citations = [str(item) for item in (result.get("citations") or []) if item]

    parts = [f"✅ {done_label}", f"范围：{scope}；模式：{mode}", "", answer]
    if citations:
        parts.extend(["", "引用：", *[f"- {item}" for item in citations]])
    notice = str(result.get("answer_notice") or "").strip()
    if notice:
        parts.extend(["", f"⚠️ {notice}"])
    return "\n".join(parts)


__all__ = ["ChatAgentService"]
