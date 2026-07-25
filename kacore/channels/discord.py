"""Discord 渠道 adapter；业务全部委派给 ConversationService。"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from kacore.channels.base import ChannelStatus, ConversationChannel
from kacore.domain.conversation import ConversationTurn
from kacore.utils.text_chunks import split_message_text

if TYPE_CHECKING:
    from kacore.config import DiscordConfig
    from kacore.conversation import ConversationService

logger = logging.getLogger("DiscordChannel")

_MESSAGE_LIMIT = 1900


class DiscordChannel(ConversationChannel):
    """discord.py Gateway adapter；仅处理私信、提及和回复 Bot 的文本消息。"""

    def __init__(
        self,
        service: ConversationService,
        config: DiscordConfig,
        token: str,
    ) -> None:
        self._service = service
        self._config = config
        self._token = token
        self._client: Any | None = None
        self._task: asyncio.Task[Any] | None = None
        self._status = ChannelStatus(enabled=config.enabled)

    async def start(self) -> None:
        if not self._config.enabled:
            self._status.detail = "disabled"
            return
        if not self._token:
            self._status.detail = "bot token is not configured"
            return
        if not self._config.allowed_user_ids:
            self._status.detail = "allowed_user_ids must contain at least one user"
            return
        try:
            import discord  # type: ignore[import-not-found]
        except ImportError:
            self._status.detail = "discord.py is not installed"
            return

        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event  # type: ignore[untyped-decorator]
        async def on_ready() -> None:
            self._status.connected = True
            self._status.detail = f"connected as {client.user}"

        @client.event  # type: ignore[untyped-decorator]
        async def on_disconnect() -> None:
            self._status.connected = False
            self._status.detail = "gateway disconnected; reconnecting"

        @client.event  # type: ignore[untyped-decorator]
        async def on_message(message: Any) -> None:
            await self._on_message(message)

        self._status.running = True
        self._status.detail = "connecting"
        self._task = asyncio.create_task(self._run_client(client))

    async def _run_client(self, client: Any) -> None:
        try:
            await client.start(self._token, reconnect=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - Gateway 错误写入公开诊断，禁止泄露 token
            logger.error("Discord gateway stopped: %s", exc)
            self._status.detail = f"gateway error: {type(exc).__name__}: {exc}"
        finally:
            self._status.running = False
            self._status.connected = False

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # noqa: BLE001 - teardown 必须继续
                logger.warning("Discord close failed: %s", exc)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._client = None
        self._status.running = False
        self._status.connected = False

    def status(self) -> ChannelStatus:
        return ChannelStatus(**self._status.__dict__)

    async def _on_message(self, message: Any) -> None:
        client_user = getattr(self._client, "user", None)
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return
        user_id = str(getattr(author, "id", ""))
        if user_id not in self._config.allowed_user_ids:
            return
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        if channel is None:
            return
        guild_id = str(getattr(guild, "id", "")) if guild is not None else ""
        channel_id = str(getattr(channel, "id", ""))
        if guild is not None:
            if self._config.allowed_guild_ids and guild_id not in self._config.allowed_guild_ids:
                return
            parent_id = str(getattr(getattr(channel, "parent", None), "id", ""))
            allowed_channel = channel_id in self._config.allowed_channel_ids or (
                parent_id and parent_id in self._config.allowed_channel_ids
            )
            if self._config.allowed_channel_ids and not allowed_channel:
                return
            mentioned = client_user is not None and client_user in getattr(message, "mentions", [])
            replied = await _is_reply_to_bot(message, client_user)
            if not mentioned and not replied:
                return
        text = _strip_bot_mention(str(getattr(message, "content", "")), client_user).strip()
        if not text:
            return
        scope_id = channel_id
        external_key = (
            f"guild:{guild_id}:scope:{scope_id}:user:{user_id}"
            if guild is not None
            else f"dm:{channel_id}:user:{user_id}"
        )
        session = await self._service.resolve_external_session("discord", external_key)
        turn = ConversationTurn(
            conversation_id=session.conversation_id,
            message=text,
            channel="discord",
            external_key=external_key,
        )
        status_message = None
        try:
            async with channel.typing():
                async for event in self._service.stream(turn):
                    if event.get("type") == "stage":
                        stage = str(event.get("text") or "")
                        if status_message is None:
                            status_message = await channel.send(
                                stage, allowed_mentions=_allowed_mentions_none()
                            )
                        else:
                            await status_message.edit(content=stage)
                    elif event.get("type") == "assistant":
                        text_out = _format_event(event)
                        for part in split_message_text(text_out, limit=_MESSAGE_LIMIT):
                            await channel.send(part, allowed_mentions=_allowed_mentions_none())
                    elif event.get("type") == "error":
                        await channel.send(
                            f"⚠ {event.get('message') or '处理失败'}",
                            allowed_mentions=_allowed_mentions_none(),
                        )
        finally:
            if status_message is not None:
                try:
                    await status_message.delete()
                except Exception:
                    pass


async def _is_reply_to_bot(message: Any, client_user: Any) -> bool:
    reference = getattr(message, "reference", None)
    if reference is None or client_user is None:
        return False
    resolved = getattr(reference, "resolved", None)
    if resolved is not None:
        return getattr(getattr(resolved, "author", None), "id", None) == getattr(
            client_user, "id", None
        )
    try:
        original = await message.channel.fetch_message(reference.message_id)
    except Exception:
        return False
    return getattr(getattr(original, "author", None), "id", None) == getattr(
        client_user, "id", None
    )


def _strip_bot_mention(text: str, client_user: Any) -> str:
    user_id = getattr(client_user, "id", None)
    if user_id is None:
        return text
    return re.sub(rf"<@!?{re.escape(str(user_id))}>", "", text)


def _allowed_mentions_none() -> Any:
    import discord

    return discord.AllowedMentions.none()


def _format_event(event: dict[str, Any]) -> str:
    content = str(event.get("content") or "")
    scope = event.get("retrieval_scope") or {}
    kind = scope.get("kind")
    names = [str(name) for name in scope.get("collection_names") or []]
    if kind == "global":
        footer = f"全局 · {len(names)} collections"
    elif kind == "collection":
        requested = scope.get("requested_collection") or (names[0] if names else "")
        footer = f"范围：{requested}" + ("（含子集合）" if scope.get("include_descendants") else "")
    elif kind == "none":
        footer = "未执行知识库召回"
    else:
        footer = "范围未知"
    notice = str(event.get("answer_notice") or "").strip()
    parts = [content, f"\n— {footer}"]
    if notice:
        parts.append(f"⚠ {notice}")
    return "\n".join(part for part in parts if part)


__all__ = ["DiscordChannel"]
