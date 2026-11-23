"""两种连接的原生工具调用：工具声明原样发出、工具调用解析成中立轮次、助手原始消息原样回放。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kacore.domain.tool_calls import (
    AssistantTurn,
    ResultTurn,
    ToolCallRequest,
    ToolSpec,
    UserTurn,
)
from kacore.providers.anthropic import AnthropicChat
from kacore.providers.base import ERROR_NO_TOOLS, ProviderError
from kacore.providers.openai_compat import OpenAICompatChat

SPEC = ToolSpec("search", "Search.",
                {"type": "object", "properties": {"query": {"type": "string"}}})


async def test_openai_compat_sends_tools_and_parses_tool_calls() -> None:
    chat = OpenAICompatChat("https://x/v1", "m", max_retries=0)
    sent: list[dict[str, Any]] = []

    async def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent.append(payload)
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_content": "think",
            "tool_calls": [{"id": "t1", "type": "function",
                            "function": {"name": "search", "arguments": '{"query": "a"}'}}]}}]}

    chat._post_json = post  # type: ignore[method-assign]
    earlier = AssistantTurn("", (ToolCallRequest("t0", "search", {"query": "z"}),),
                            raw={"role": "assistant", "content": None, "reasoning_content": "r0"})
    turn = await chat.generate_with_tools(
        [UserTurn("q"), earlier, ResultTurn("t0", "result")], [SPEC], "sys")
    assert turn.calls == (ToolCallRequest("t1", "search", {"query": "a"}),)
    assert turn.result.status == "ok" and turn.raw["reasoning_content"] == "think"
    payload = sent[0]
    assert payload["tools"][0]["function"]["parameters"] == SPEC.input_schema
    assert payload["messages"][2] == earlier.raw                      # 原始消息原样回放
    assert payload["messages"][3] == {"role": "tool", "tool_call_id": "t0", "content": "result"}


async def test_openai_compat_endpoint_rejecting_tools_is_reported_as_no_tools() -> None:
    chat = OpenAICompatChat("https://x/v1", "m", max_retries=0)

    async def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError("bad", "tools not supported", status=400)

    chat._post_json = post  # type: ignore[method-assign]
    with pytest.raises(ProviderError) as info:
        await chat.generate_with_tools([UserTurn("q")], [SPEC])
    assert info.value.code == ERROR_NO_TOOLS


async def test_anthropic_merges_tool_results_into_one_user_message_and_replays_blocks() -> None:
    tool_use = SimpleNamespace(type="tool_use", id="u2", name="search", input={"query": "b"},
                               model_dump=lambda **_k: {"type": "tool_use", "id": "u2"})
    response = SimpleNamespace(content=[tool_use], stop_reason="tool_use", model="m",
                               usage=SimpleNamespace(input_tokens=3, output_tokens=2))
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return response

    sdk = SimpleNamespace(messages=SimpleNamespace(create=create),
                          beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    chat = AnthropicChat("https://gateway.example", "claude-x", "k", client=sdk)
    replay = [{"type": "thinking", "thinking": "t", "signature": "s"},
              {"type": "tool_use", "id": "u0", "name": "search", "input": {}},
              {"type": "tool_use", "id": "u1", "name": "search", "input": {}}]
    turn = await chat.generate_with_tools(
        [UserTurn("q"), AssistantTurn("", (), raw=replay),
         ResultTurn("u0", "r0"), ResultTurn("u1", "r1", is_error=True), UserTurn("note")],
        [SPEC])
    assert turn.calls == (ToolCallRequest("u2", "search", {"query": "b"}),)
    assert turn.result.status == "ok" and turn.raw == [{"type": "tool_use", "id": "u2"}]
    messages = calls[0]["messages"]
    assert messages[1]["content"] == replay                              # 思考块原样交回
    assert [b["tool_use_id"] for b in messages[2]["content"][:2]] == ["u0", "u1"]
    assert messages[2]["content"][1]["is_error"] is True
    assert len(messages) == 3                                            # 提示并进同一条 user 消息
    assert messages[2]["content"][2] == {"type": "text", "text": "note"}
    assert calls[0]["tools"][0]["input_schema"] == SPEC.input_schema
