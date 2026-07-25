"""统一 ConversationService 契约测试。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kacore.domain.conversation import ConversationTurn
from kacore.plugin_initializer import PluginInitializer


class _ScriptedLLM:
    def __init__(self, rounds: list[list[dict]] | None = None, rewritten: str = "") -> None:
        self.rounds = rounds or []
        self.rewritten = rewritten
        self.calls: list[list[dict]] = []

    async def generate(self, prompt: str, system_prompt: str = "", **kwargs: object) -> str:
        return self.rewritten

    async def chat_stream(self, messages: list[dict], *, tools=None, temperature=0.3):
        self.calls.append([dict(item) for item in messages])
        for event in self.rounds[len(self.calls) - 1]:
            yield event


async def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, llm=None) -> PluginInitializer:
    monkeypatch.setattr("kacore.plugin_initializer._module_available", lambda name: False)
    initializer = PluginInitializer(
        None,
        {"web_console": {"enabled": False}},
        tmp_path,
        llm_adapter=llm,
        shell="standalone",
    )
    await asyncio.wait_for(initializer.initialize(), timeout=10)
    return initializer


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


async def test_conversation_service_attached_and_session_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, _ScriptedLLM())
    try:
        assert initializer.conversation_service is not None
        assert initializer.api.conversation_available()
        session = await initializer.conversation_service.get_or_create_session("conv-1")
        assert session.research_enabled is False
        assert session.context_enabled is True
        assert session.persona_enabled is False
    finally:
        await asyncio.wait_for(initializer.teardown(), timeout=10)


async def test_session_settings_persist_and_clear_resets_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, _ScriptedLLM())
    try:
        service = initializer.conversation_service
        await service.update_session("conv-2", {"research_enabled": True, "persona_enabled": True})
        assert (await service.get_or_create_session("conv-2")).research_enabled is True
        await initializer.api.clear_chat_history("conv-2")
        reset = await service.get_or_create_session("conv-2")
        assert reset.research_enabled is False and reset.persona_enabled is False
    finally:
        await asyncio.wait_for(initializer.teardown(), timeout=10)


async def test_normal_turn_persists_once_with_scope_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, _ScriptedLLM())
    try:
        events = await _collect(
            initializer.conversation_service.stream(
                ConversationTurn("conv-3", "没有命中的问题", collection="default")
            )
        )
        assert [event["type"] for event in events][-1] == "done"
        history = await initializer.api.get_chat_history("conv-3")
        assert [item["role"] for item in history] == ["user", "assistant"]
        scope = history[-1]["metadata"]["retrieval_scope"]
        assert scope["kind"] == "collection" and scope["requested_collection"] == "default"
    finally:
        await asyncio.wait_for(initializer.teardown(), timeout=10)


async def test_context_rewrite_is_used_but_not_persisted_as_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _ScriptedLLM(rewritten="self-contained retrieval question")
    initializer = await _build(tmp_path, monkeypatch, llm)
    try:
        await initializer.source_store.add_chat_message("conv-4", "user", "earlier subject")
        await initializer.source_store.add_chat_message("conv-4", "assistant", "earlier answer")
        await _collect(
            initializer.conversation_service.stream(ConversationTurn("conv-4", "它有什么限制？"))
        )
        history = await initializer.api.get_chat_history("conv-4")
        assert history[-1]["metadata"]["context_applied"] is True
        assert all(
            "self-contained retrieval question" not in str(item.get("sources"))
            for item in history
        )
    finally:
        await asyncio.wait_for(initializer.teardown(), timeout=10)


async def test_research_tool_delivery_has_no_duplicate_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _ScriptedLLM(
        [[{
            "type": "final",
            "content": "",
            "tool_calls": [{
                "id": "call_0",
                "name": "research_execute",
                "arguments": json.dumps({"query": "topic", "mode": "default"}),
            }],
            "finish_reason": "tool_calls",
        }]]
    )
    initializer = await _build(tmp_path, monkeypatch, llm)
    try:
        await initializer.conversation_service.update_session(
            "conv-5", {"research_enabled": True, "context_enabled": False}
        )
        events = await _collect(
            initializer.conversation_service.stream(
                ConversationTurn("conv-5", "research topic", collection="default")
            )
        )
        assert any(event["type"] == "assistant" for event in events)
        history = await initializer.api.get_chat_history("conv-5")
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[-1]["metadata"]["turn_kind"] == "research"
    finally:
        await asyncio.wait_for(initializer.teardown(), timeout=10)
