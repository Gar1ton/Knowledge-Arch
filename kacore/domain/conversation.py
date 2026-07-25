"""统一对话领域模型（domain 层，零业务依赖）。

这些值对象固定 Web、Discord 与未来渠道共享的会话设置、输入和回答范围契约；
渠道 SDK、数据库和 HTTP 形状都不得进入本模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ConversationSession:
    """一个持久化会话及其按会话生效的三个行为开关。"""

    conversation_id: str
    channel: str = "web"
    external_key: str = ""
    research_enabled: bool = False
    context_enabled: bool = True
    persona_enabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class RetrievalScopeSnapshot:
    """一次回答实际使用的召回范围快照；历史回放不得重新推断。"""

    kind: str = "none"
    requested_collection: str | None = None
    collection_names: tuple[str, ...] = ()
    include_descendants: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "requested_collection": self.requested_collection,
            "collection_names": list(self.collection_names),
            "include_descendants": self.include_descendants,
        }


@dataclass(frozen=True)
class ConversationTurn:
    """渠道无关的一轮输入；调用方选择范围，服务端读取持久化会话开关。"""

    conversation_id: str
    message: str
    channel: str = "web"
    external_key: str = ""
    collection: str | None = None
    document_id: str | None = None
    retrieval_mode: str = "default"
    answer_language: str = "auto"
    use_english_retrieval: bool = True


@dataclass(frozen=True)
class ConversationEvent:
    """统一事件流载荷；data 只包含 JSON 可序列化值。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}


__all__ = [
    "ConversationEvent",
    "ConversationSession",
    "ConversationTurn",
    "RetrievalScopeSnapshot",
]
