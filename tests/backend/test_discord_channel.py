"""Discord channel adapter contract tests without a live Gateway connection."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from kacore.channels.discord import DiscordChannel
from kacore.config import DiscordConfig
from kacore.domain.conversation import ConversationSession


class _Service:
    def __init__(self, answer: str = "answer") -> None:
        self.answer = answer
        self.external_keys: list[str] = []
        self.turns = []

    async def resolve_external_session(self, channel: str, external_key: str):
        assert channel == "discord"
        self.external_keys.append(external_key)
        return ConversationSession("internal-session", channel=channel, external_key=external_key)

    async def stream(self, turn):
        self.turns.append(turn)
        yield {"type": "stage", "text": "probing"}
        yield {"type": "stage", "text": "retrieving"}
        yield {
            "type": "assistant",
            "content": self.answer,
            "retrieval_scope": {
                "kind": "collection",
                "requested_collection": "papers",
                "collection_names": ["papers"],
                "include_descendants": True,
            },
        }
        yield {"type": "done"}


class _StatusMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.deleted = False

    async def edit(self, *, content: str) -> None:
        self.content = content

    async def delete(self) -> None:
        self.deleted = True


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent = SimpleNamespace(id=parent_id) if parent_id else None
        self.sent: list[tuple[str, object]] = []
        self.status_messages: list[_StatusMessage] = []

    @asynccontextmanager
    async def typing(self):
        yield

    async def send(self, content: str, *, allowed_mentions: object):
        self.sent.append((content, allowed_mentions))
        status = _StatusMessage(content)
        self.status_messages.append(status)
        return status

    async def fetch_message(self, message_id: int):
        return SimpleNamespace(author=SimpleNamespace(id=999))


def _message(
    channel: _Channel,
    *,
    text: str,
    guild_id: int | None,
    mentions: list[object] | None = None,
    reference: object | None = None,
    user_id: int = 123,
):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id, bot=False),
        guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
        channel=channel,
        content=text,
        mentions=mentions or [],
        reference=reference,
    )


async def test_discord_dm_isolated_by_user_and_channel_and_splits_safely(monkeypatch) -> None:
    service = _Service(answer="sentence. " * 500)
    adapter = DiscordChannel(
        service,
        DiscordConfig(enabled=True, allowed_user_ids=("123",)),
        "token",
    )
    bot = SimpleNamespace(id=999)
    adapter._client = SimpleNamespace(user=bot)
    monkeypatch.setattr("kacore.channels.discord._allowed_mentions_none", lambda: "none")
    channel = _Channel(77)

    await adapter._on_message(_message(channel, text="hello", guild_id=None))

    assert service.external_keys == ["dm:77:user:123"]
    assert service.turns[0].message == "hello"
    answer_parts = [content for content, _ in channel.sent if content != "probing"]
    assert len(answer_parts) > 1
    assert all(len(part) <= 1900 for part in answer_parts)
    assert all(mentions == "none" for _, mentions in channel.sent)
    assert channel.status_messages[0].content == "retrieving"
    assert channel.status_messages[0].deleted is True


async def test_discord_guild_requires_mention_or_reply_and_honours_allowlists(monkeypatch) -> None:
    service = _Service()
    adapter = DiscordChannel(
        service,
        DiscordConfig(
            enabled=True,
            allowed_user_ids=("123",),
            allowed_guild_ids=("5",),
            allowed_channel_ids=("10",),
        ),
        "token",
    )
    bot = SimpleNamespace(id=999)
    adapter._client = SimpleNamespace(user=bot)
    monkeypatch.setattr("kacore.channels.discord._allowed_mentions_none", lambda: "none")
    thread = _Channel(11, parent_id=10)

    await adapter._on_message(_message(thread, text="ordinary", guild_id=5))
    assert service.turns == []

    await adapter._on_message(
        _message(thread, text="<@999> mentioned", guild_id=5, mentions=[bot])
    )
    assert service.external_keys[-1] == "guild:5:scope:11:user:123"
    assert service.turns[-1].message == "mentioned"

    reply = SimpleNamespace(resolved=SimpleNamespace(author=SimpleNamespace(id=999)))
    await adapter._on_message(
        _message(thread, text="follow up", guild_id=5, reference=reply)
    )
    assert service.turns[-1].message == "follow up"

    await adapter._on_message(
        _message(thread, text="<@999> blocked", guild_id=6, mentions=[bot])
    )
    await adapter._on_message(
        _message(thread, text="<@999> blocked user", guild_id=5, mentions=[bot], user_id=456)
    )
    assert len(service.turns) == 2


async def test_discord_start_diagnostics_do_not_block_standalone() -> None:
    service = _Service()
    missing_token = DiscordChannel(
        service, DiscordConfig(enabled=True, allowed_user_ids=("123",)), ""
    )
    await missing_token.start()
    assert missing_token.status().detail == "bot token is not configured"

    missing_allowlist = DiscordChannel(service, DiscordConfig(enabled=True), "token")
    await missing_allowlist.start()
    assert "allowed_user_ids" in missing_allowlist.status().detail

    disabled = DiscordChannel(service, DiscordConfig(enabled=False), "")
    await disabled.start()
    assert disabled.status().detail == "disabled"
