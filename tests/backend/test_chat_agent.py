"""对话 Agent 服务契约测试：命令分发、事件流、工具循环与持久化。

用脚本化 fake LLM（chat_stream 事件序列注入）驱动真实组合根（module_available=False
跳过重依赖），验证与 apps/plugin/main.py 对齐的行为语义。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ka_core.plugin_initializer import PluginInitializer


class _ScriptedLLM:
    """chat_stream 按预置脚本逐轮回放；记录收到的 messages 供断言。"""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self._rounds = rounds
        self.calls: list[list[dict]] = []

    async def generate(self, prompt: str, system_prompt: str = "", **kwargs: object) -> str:
        return ""

    async def chat_stream(self, messages: list[dict], *, tools=None, temperature=0.3):
        self.calls.append([dict(m) for m in messages])
        for event in self._rounds[len(self.calls) - 1]:
            yield event


async def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, llm) -> PluginInitializer:
    monkeypatch.setattr("ka_core.plugin_initializer._module_available", lambda name: False)
    initializer = PluginInitializer(
        None, {"web_console": {"enabled": False}}, tmp_path, llm_adapter=llm
    )
    await initializer.initialize()
    return initializer


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


async def test_chat_agent_attached_when_llm_supports_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _ScriptedLLM([])
    initializer = await _build(tmp_path, monkeypatch, llm)
    try:
        assert initializer.chat_agent is not None
        assert initializer.api is not None and initializer.api.chat_agent_available()
    finally:
        await initializer.teardown()


async def test_chat_agent_ka_command_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, _ScriptedLLM([]))
    try:
        events = await _collect(initializer.chat_agent.stream("/ka help", "conv-1"))
        assistant = [e for e in events if e["type"] == "assistant"]
        assert assistant and "/ka 指令一览" in assistant[0]["content"]
        assert events[-1]["type"] == "done"
        history = await initializer.api.get_chat_history("conv-1")
        assert [m["role"] for m in history] == ["user", "assistant"]
        # webui 命令在独立壳被守卫，不真正启停控制台。
        events = await _collect(initializer.chat_agent.stream("/ka webui off", "conv-1"))
        assistant = [e for e in events if e["type"] == "assistant"]
        assert "独立版本体即 WebUI" in assistant[0]["content"]
    finally:
        await initializer.teardown()


async def test_chat_agent_plain_reply_streams_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _ScriptedLLM(
        [
            [
                {"type": "delta", "text": "你好"},
                {"type": "delta", "text": "！"},
                {"type": "final", "content": "你好！", "tool_calls": [], "finish_reason": "stop"},
            ]
        ]
    )
    initializer = await _build(tmp_path, monkeypatch, llm)
    try:
        events = await _collect(initializer.chat_agent.stream("在吗", "conv-2"))
        deltas = "".join(e["text"] for e in events if e["type"] == "delta")
        assert deltas == "你好！"
        assistant = [e for e in events if e["type"] == "assistant"]
        assert assistant[0]["content"] == "你好！"
        history = await initializer.api.get_chat_history("conv-2")
        assert history[-1]["content"] == "你好！"
        # system prompt 含工具指引；本轮 agent_enabled 默认 off → 不含注入块。
        system = llm.calls[0][0]
        assert system["role"] == "system"
        assert "research_scope_probe" in system["content"]
        assert "Knowledge Base Context" not in system["content"]
    finally:
        await initializer.teardown()


async def test_chat_agent_tool_loop_research_disabled_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _ScriptedLLM(
        [
            [
                {
                    "type": "final",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "name": "research_scope_probe",
                            "arguments": json.dumps({"query": "SHAP"}),
                        }
                    ],
                    "finish_reason": "tool_calls",
                }
            ],
            [
                {
                    "type": "final",
                    "content": "research 尚未开启，请先发送 /ka research on。",
                    "tool_calls": [],
                    "finish_reason": "stop",
                }
            ],
        ]
    )
    initializer = await _build(tmp_path, monkeypatch, llm)
    try:
        events = await _collect(initializer.chat_agent.stream("查一下 SHAP", "conv-3"))
        stages = [e for e in events if e["type"] == "stage"]
        assert stages and "探查" in stages[0]["text"]
        # 第二轮请求应携带 tool 角色消息（research 未开启的通知回喂模型）。
        second_call = llm.calls[1]
        tool_msgs = [m for m in second_call if m.get("role") == "tool"]
        assert tool_msgs and "research 未开启" in tool_msgs[0]["content"]
        assistant = [e for e in events if e["type"] == "assistant"]
        assert "research 尚未开启" in assistant[-1]["content"]
    finally:
        await initializer.teardown()
