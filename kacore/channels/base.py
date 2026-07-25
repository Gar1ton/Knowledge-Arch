"""外部对话渠道端口；实现只能做事件翻译、访问控制与发送。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IncomingMessage:
    """外部平台输入的最小稳定形状。"""

    channel: str
    external_key: str
    user_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelStatus:
    """公开运行状态；不得包含 token 或消息正文。"""

    enabled: bool = False
    running: bool = False
    connected: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "connected": self.connected,
            "detail": self.detail,
        }


class ConversationChannel(ABC):
    """组合根管理的外部渠道生命周期。"""

    @abstractmethod
    async def start(self) -> None:
        """非阻塞启动渠道；连接循环由实现自己的后台任务维持。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止后台连接并释放资源；重复调用幂等。"""
        ...

    @abstractmethod
    def status(self) -> ChannelStatus:
        """返回无机密的当前运行状态。"""
        ...


__all__ = ["ChannelStatus", "ConversationChannel", "IncomingMessage"]

