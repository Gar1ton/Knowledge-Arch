"""「模型思考」开关：按连接换算关闭参数、被拒时去掉重试、只作用于关掉它的那一问。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from kacore.domain.llm_generation import STATUS_OK
from kacore.providers.anthropic import AnthropicChat
from kacore.providers.openai_compat import OpenAICompatChat
from kacore.providers.thinking import (
    anthropic_thinking_off,
    model_thinking,
    openai_compat_thinking_off,
)

# ── 参数换算 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # 关不掉思考（或关闭与拒答回退冲突）的型号：压到最低 effort
        ("claude-opus-5-5", {"output_config": {"effort": "low"}}),
        ("claude-fable-5-1", {"output_config": {"effort": "low"}}),
        ("claude-sonnet-5-5", {"output_config": {"effort": "low"}}),
        ("claude-opus-5", {"output_config": {"effort": "low"}}),
        # 可以直接关
        ("claude-haiku-5-5", {"thinking": {"type": "disabled"}}),
        ("claude-sonnet-5", {"thinking": {"type": "disabled"}}),
        ("claude-opus-4-8", {"thinking": {"type": "disabled"}}),
        # 更早的型号缺省不思考
        ("claude-haiku-4-5", {}),
        ("claude-opus-4-6", {}),
    ],
)
def test_anthropic_params_follow_the_model(model: str, expected: dict[str, Any]) -> None:
    assert anthropic_thinking_off(model) == expected


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", {"enable_thinking": False}),
        ("https://api.siliconflow.cn/v1", {"enable_thinking": False}),
        ("https://open.bigmodel.cn/api/paas/v4", {"thinking": {"type": "disabled"}}),
        ("https://api.deepseek.com/v1", {"thinking": {"type": "disabled"}}),
        ("https://api.moonshot.cn/v1", {"thinking": {"type": "disabled"}}),
        ("https://openrouter.ai/api/v1", {"reasoning": {"effort": "none"}}),
        ("https://api.openai.com/v1", {"reasoning_effort": "none"}),
        ("http://localhost:11434/v1", {"reasoning_effort": "none"}),
        ("http://192.168.1.5:8000/v1", {"chat_template_kwargs": {"enable_thinking": False}}),
        ("http://localhost:1234/v1", {"chat_template_kwargs": {"enable_thinking": False}}),
    ],
)
def test_openai_compat_params_follow_the_provider(base_url: str, expected: dict) -> None:
    assert openai_compat_thinking_off(base_url) == expected


# ── OpenAI 兼容客户端 ─────────────────────────────────────────────


async def _server(handler: Any) -> TestServer:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    server = TestServer(app)
    await server.start_server()
    return server


def _ok(model: str) -> web.Response:
    return web.json_response({
        "model": model,
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    })


async def test_openai_compat_sends_fields_only_when_thinking_is_off() -> None:
    bodies: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        bodies.append(body)
        return _ok(body["model"])

    server = await _server(chat)
    try:
        client = OpenAICompatChat(str(server.make_url("/v1")), "qwen3", max_retries=0)
        await client.generate_result("q")
        token = model_thinking.set(False)
        try:
            await client.generate_result("q")
        finally:
            model_thinking.reset(token)
    finally:
        await server.close()
    assert "chat_template_kwargs" not in bodies[0]
    assert bodies[1]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_openai_compat_retries_without_rejected_fields_and_remembers() -> None:
    bodies: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        bodies.append(body)
        if "chat_template_kwargs" in body:
            return web.json_response({"error": "unknown field"}, status=400)
        return _ok(body["model"])

    server = await _server(chat)
    try:
        # max_retries=0：拒收后的重试不占重试次数
        client = OpenAICompatChat(str(server.make_url("/v1")), "m", max_retries=0)
        token = model_thinking.set(False)
        try:
            first = await client.generate_result("q", allow_mock=False)
            second = await client.generate_result("q", allow_mock=False)
        finally:
            model_thinking.reset(token)
    finally:
        await server.close()
    assert first.status == STATUS_OK and second.status == STATUS_OK
    assert ["chat_template_kwargs" in b for b in bodies] == [True, False, False]


# ── Anthropic 客户端 ──────────────────────────────────────────────


class _Messages:
    def __init__(self, fail_with_thinking_off: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail_with_thinking_off

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._fail and ("thinking" in kwargs or "output_config" in kwargs):
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.BadRequestError(
                "bad", response=httpx2.Response(400, request=request), body=None
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="answer")],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def _sdk(messages: _Messages) -> SimpleNamespace:
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


async def test_anthropic_adds_params_when_thinking_is_off() -> None:
    messages = _Messages()
    chat = AnthropicChat("https://api.anthropic.com", "claude-haiku-5-5", "k",
                         client=_sdk(messages))
    await chat.generate_result("q")
    token = model_thinking.set(False)
    try:
        await chat.generate_result("q")
    finally:
        model_thinking.reset(token)
    assert "thinking" not in messages.calls[0]
    assert messages.calls[1]["thinking"] == {"type": "disabled"}


async def test_anthropic_retries_without_params_on_400() -> None:
    messages = _Messages(fail_with_thinking_off=True)
    chat = AnthropicChat("https://api.anthropic.com", "claude-opus-5-5", "k",
                         client=_sdk(messages))
    token = model_thinking.set(False)
    try:
        result = await chat.generate_result("q", allow_mock=False)
    finally:
        model_thinking.reset(token)
    assert result.text == "answer"
    assert messages.calls[0]["output_config"] == {"effort": "low"}
    assert "output_config" not in messages.calls[1]


# ── 作用范围 ──────────────────────────────────────────────────────


async def test_switch_stays_inside_the_task_that_set_it() -> None:
    """ask 在自己的任务里设开关：同时进行的其他请求不受影响。"""
    seen: dict[str, bool] = {}

    async def ask(name: str, thinking: bool) -> None:
        model_thinking.set(thinking)
        await asyncio.sleep(0)
        seen[name] = model_thinking.get()

    await asyncio.gather(
        asyncio.create_task(ask("off", False)), asyncio.create_task(ask("on", True))
    )
    assert seen == {"off": False, "on": True}
    assert model_thinking.get() is True
