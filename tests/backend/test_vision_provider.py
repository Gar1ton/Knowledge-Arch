"""识图角色与图片调用（2.1.0 P4）：两种连接的图文消息、拒收图片的分类、vision 不跟随对话模型。"""
from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from kacore.domain.llm_generation import STATUS_OK
from kacore.providers.anthropic import AnthropicChat
from kacore.providers.base import ERROR_NO_IMAGE_INPUT, ERROR_NOT_FOUND, ProviderError
from kacore.providers.openai_compat import OpenAICompatChat
from kacore.providers.registry import ProviderRegistry
from kacore.providers.router import ModelRouter
from kacore.settings.store import SettingsStore

PNG = b"\x89PNG\r\n\x1a\nfake"


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _fake_sdk(text: str) -> SimpleNamespace:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=900, output_tokens=9),
    )
    return SimpleNamespace(
        messages=_FakeMessages(response), beta=SimpleNamespace(messages=_FakeMessages(response))
    )


async def _server(handler: Any) -> TestServer:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_openai_compat_sends_png_as_data_uri() -> None:
    seen: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({
            "choices": [{"message": {"content": "A bar chart."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 12},
        })

    server = await _server(handler)
    try:
        chat = OpenAICompatChat(str(server.make_url("/v1")), "qwen-vl", "sk")
        result = await chat.describe_image("Describe.", PNG, "sys", max_tokens=300)
    finally:
        await server.close()
    assert (result.text, result.status, result.prompt_tokens) == ("A bar chart.", STATUS_OK, 900)
    payload = seen[0]
    assert payload["max_tokens"] == 300
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    text_part, image_part = payload["messages"][1]["content"]
    assert text_part == {"type": "text", "text": "Describe."}
    assert image_part["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    )


async def test_openai_compat_classifies_rejected_images() -> None:
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if any(k in body for k in ("enable_thinking", "chat_template_kwargs", "thinking",
                                   "reasoning", "reasoning_effort")):
            return web.json_response({"error": "unknown field"}, status=400)
        return web.json_response({"error": "image input is not supported"}, status=400)

    server = await _server(handler)
    try:
        chat = OpenAICompatChat(str(server.make_url("/v1")), "text-only", "sk")
        with pytest.raises(ProviderError) as info:
            await chat.describe_image("Describe.", PNG)
    finally:
        await server.close()
    assert info.value.code == ERROR_NO_IMAGE_INPUT
    assert "image input is not supported" in info.value.message


async def test_anthropic_sends_image_block_before_text() -> None:
    sdk = _fake_sdk("Two loss curves.")
    chat = AnthropicChat("https://gateway.example", "claude-haiku-5-5", "k", client=sdk)

    result = await chat.describe_image("Describe.", PNG, "sys", max_tokens=400)

    assert result.text == "Two loss curves."
    call = sdk.messages.calls[0]
    image, text = call["messages"][0]["content"]
    assert image["source"] == {
        "type": "base64", "media_type": "image/png",
        "data": base64.b64encode(PNG).decode("ascii"),
    }
    assert text == {"type": "text", "text": "Describe."}
    assert call["max_tokens"] == 400 and call["system"] == "sys"


async def test_vision_role_never_follows_chat() -> None:
    settings = SettingsStore.in_memory()
    registry = ProviderRegistry(settings, client_factory=lambda *args: None)  # type: ignore[arg-type,return-value]
    registry.create(label="A", base_url="https://a.example/v1", provider_id="a", api_key="ka")
    settings.set("flow.models.chat", {"provider_id": "a", "model": "chat-m"})
    router = ModelRouter(settings, registry)

    vision = router.client_for("vision")
    assert router.ref_for("research") is not None  # research 跟随 chat
    assert router.ref_for("vision") is None
    assert not vision.configured
    with pytest.raises(ProviderError) as info:
        await vision.describe_image("Describe.", PNG)
    assert info.value.code == ERROR_NOT_FOUND

    settings.set("flow.models.vision", {"provider_id": "a", "model": "vl-m"})
    assert vision.configured
    assert router.describe("vision") == router.ref_for("vision").label


def test_base_client_rejects_images_by_default() -> None:
    from kacore.providers.base import ProviderClient

    class TextOnly(ProviderClient):
        async def generate_result(self, prompt, system_prompt="", *, allow_mock=True):
            return SimpleNamespace()

        async def list_models(self):
            return []

        async def ping(self, model):
            return None

    import asyncio

    with pytest.raises(ProviderError) as info:
        asyncio.run(TextOnly().describe_image("x", PNG))
    assert info.value.code == ERROR_NO_IMAGE_INPUT
