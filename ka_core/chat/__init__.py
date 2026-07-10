"""对话 Agent 子系统（event_handler 层）。

独立版的「AstrBot 聊天等价物」：/ka 命令分发 + agent 上下文注入 + research/notion
工具循环，经 ka_web 的 SSE 路由暴露给对话界面。业务能力全部委派既有 api/managers。
"""
from __future__ import annotations

from ka_core.chat.agent_service import ChatAgentService

__all__ = ["ChatAgentService"]
