"""模型供应商：OpenAI 兼容 / Anthropic 客户端、连接注册表（测试连接 / 模型列表）、按角色路由。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from kacore.adapters.llm import LLMAdapter
from kacore.domain.llm_generation import (
    STATUS_CONTENT_FILTER,
    STATUS_LENGTH,
    STATUS_OK,
    STATUS_REASONING_ONLY,
    GenerationResult,
)
from kacore.providers.anthropic import AnthropicChat, classify_anthropic_error
from kacore.providers.base import (
    ERROR_AUTH,
    ERROR_NO_KEY,
    ERROR_NOT_FOUND,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    ERROR_UNREACHABLE,
    ProviderClient,
    ProviderError,
    ProviderProfile,
)
from kacore.providers.openai_compat import OpenAICompatChat
from kacore.providers.registry import ProviderRegistry
from kacore.providers.router import ModelRouter
from kacore.settings.store import SettingsStore

# ── OpenAI 兼容客户端（真实 HTTP，本地测试服务器）──────────────────


async def _server(routes: dict[str, Any]) -> TestServer:
    app = web.Application()
    for (method, path), handler in routes.items():
        app.router.add_route(method, path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_openai_compat_lists_models_and_sends_bearer_only_when_set() -> None:
    seen: list[str | None] = []

    async def models(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]})

    server = await _server({("GET", "/v1/models"): models})
    try:
        url = str(server.make_url("/v1"))
        assert await OpenAICompatChat(url, "", "sk-1").list_models() == ["a", "b"]
        assert await OpenAICompatChat(url, "").list_models() == ["a", "b"]
    finally:
        await server.close()
    assert seen == ["Bearer sk-1", None]


@pytest.mark.parametrize(
    ("status", "code"), [(401, ERROR_AUTH), (403, ERROR_AUTH), (404, ERROR_NOT_FOUND),
                         (429, ERROR_RATE_LIMITED)],
)
async def test_openai_compat_classifies_http_errors(status: int, code: str) -> None:
    async def models(request: web.Request) -> web.Response:
        return web.json_response({"error": "x"}, status=status)

    server = await _server({("GET", "/v1/models"): models})
    try:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatChat(str(server.make_url("/v1")), "", "bad").list_models()
    finally:
        await server.close()
    assert caught.value.code == code and caught.value.status == status


async def test_openai_compat_does_not_retry_client_errors_but_retries_server_errors() -> None:
    calls = {"n": 0}

    async def chat(request: web.Request) -> web.Response:
        calls["n"] += 1
        body = await request.json()
        if body["model"] == "bad-key":
            return web.json_response({"error": "nope"}, status=401)
        if calls["n"] == 2:
            return web.json_response({"error": "busy"}, status=503)
        return web.json_response({
            "model": body["model"],
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    server = await _server({("POST", "/v1/chat/completions"): chat})
    try:
        url = str(server.make_url("/v1"))
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatChat(url, "bad-key", max_retries=3,
                                   retry_backoff_seconds=0).generate_result("q", allow_mock=False)
        assert caught.value.code == ERROR_AUTH and calls["n"] == 1
        result = await OpenAICompatChat(url, "good", max_retries=2,
                                        retry_backoff_seconds=0).generate_result("q")
        assert result.text == "hi" and result.status == STATUS_OK and calls["n"] == 3
    finally:
        await server.close()


async def test_openai_compat_unreachable_endpoint_is_classified() -> None:
    client = OpenAICompatChat("http://127.0.0.1:9/v1", "m", max_retries=0)
    with pytest.raises(ProviderError) as caught:
        await client.list_models()
    assert caught.value.code == ERROR_UNREACHABLE


# ── Anthropic 客户端（官方 SDK，注入假客户端）────────────────────────


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _fake_sdk(response: Any) -> SimpleNamespace:
    messages = _FakeMessages(response)
    beta_messages = _FakeMessages(response)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=beta_messages))


def _message(*blocks: Any, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def _text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


async def test_anthropic_builds_result_without_sampling_params_and_with_fallbacks() -> None:
    sdk = _fake_sdk(_message(SimpleNamespace(type="thinking", thinking=""), _text("answer")))
    chat = AnthropicChat("https://api.anthropic.com", "claude-opus-5-5", "k", client=sdk)
    result = await chat.generate_result("q", "sys")
    assert (result.text, result.status, result.prompt_tokens, result.completion_tokens) == (
        "answer", STATUS_OK, 11, 7
    )
    assert not result.reasoning_present
    call = sdk.beta.messages.calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["system"] == "sys" and call["max_tokens"] == 16000
    assert "temperature" not in call and "thinking" not in call
    assert sdk.messages.calls == []


async def test_anthropic_skips_fallbacks_on_custom_gateways_and_other_models() -> None:
    sdk = _fake_sdk(_message(_text("ok")))
    await AnthropicChat("https://gateway.example", "claude-opus-5-5", "k",
                        client=sdk).generate_result("q")
    await AnthropicChat("https://api.anthropic.com", "claude-haiku-5-5", "k",
                        client=sdk).generate_result("q")
    assert len(sdk.messages.calls) == 2 and sdk.beta.messages.calls == []


@pytest.mark.parametrize(
    ("blocks", "stop", "status"),
    [
        ((SimpleNamespace(type="thinking", thinking=""),), "end_turn", STATUS_REASONING_ONLY),
        ((_text("partial"),), "max_tokens", STATUS_LENGTH),
        ((), "refusal", STATUS_CONTENT_FILTER),
    ],
)
async def test_anthropic_maps_stop_reasons(blocks: tuple, stop: str, status: str) -> None:
    sdk = _fake_sdk(_message(*blocks, stop=stop))
    result = await AnthropicChat("https://api.anthropic.com", "claude-sonnet-5-5", "k",
                                 client=sdk).generate_result("q")
    assert result.status == status


def _status_error(cls: type, status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (lambda: _status_error(anthropic.AuthenticationError, 401), ERROR_AUTH),
        (lambda: _status_error(anthropic.NotFoundError, 404), ERROR_NOT_FOUND),
        (lambda: _status_error(anthropic.RateLimitError, 429), ERROR_RATE_LIMITED),
        (lambda: anthropic.APITimeoutError(
            request=httpx2.Request("POST", "https://api.anthropic.com")), ERROR_TIMEOUT),
        (lambda: anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://api.anthropic.com")), ERROR_UNREACHABLE),
    ],
)
async def test_anthropic_errors_are_classified(exc: Any, code: str) -> None:
    error = exc()
    assert classify_anthropic_error(error).code == code
    sdk = _fake_sdk(error)
    chat = AnthropicChat("https://api.anthropic.com", "claude-opus-5-5", "k", client=sdk)
    with pytest.raises(ProviderError) as caught:
        await chat.generate_result("q", allow_mock=False)
    assert caught.value.code == code


# ── 注册表 ───────────────────────────────────────────────────────


class _StubClient(ProviderClient):
    def __init__(self, model: str, behaviour: dict[str, Any]) -> None:
        self.model = model
        self.behaviour = behaviour

    async def generate_result(self, prompt: str, system_prompt: str = "", *,
                              allow_mock: bool = True) -> GenerationResult:
        self.behaviour.setdefault("prompts", []).append((self.model, prompt))
        return GenerationResult(text=f"{self.model}:{prompt}")

    async def list_models(self) -> list[str]:
        error = self.behaviour.get("list_error")
        if error:
            raise error
        return list(self.behaviour.get("models", []))

    async def ping(self, model: str) -> None:
        self.behaviour.setdefault("pings", []).append(model)


def _registry(behaviour: dict[str, Any] | None = None) -> tuple[ProviderRegistry, SettingsStore,
                                                               dict[str, Any]]:
    state = behaviour if behaviour is not None else {}
    settings = SettingsStore.in_memory()

    def factory(profile: ProviderProfile, key: str, model: str, timeout: int,
                retries: int) -> ProviderClient:
        state.setdefault("built", []).append((profile.id, key, model, timeout, retries))
        return _StubClient(model, state)

    return ProviderRegistry(settings, client_factory=factory), settings, state


def test_registry_crud_masks_keys_and_never_writes_them_to_settings() -> None:
    registry, settings, _ = _registry()
    profile = registry.create(label="DeepSeek", base_url="https://api.deepseek.com/v1/",
                              api_key="sk-abcdefgh1234")
    assert profile.id == "deepseek" and profile.base_url == "https://api.deepseek.com/v1"
    second = registry.create(label="DeepSeek", base_url="https://api.deepseek.com/v1")
    assert second.id == "deepseek-2"
    public = registry.public(profile)
    assert public["api_key_set"] and public["api_key_masked"] == "••••1234"
    assert "api_key_ref" not in public
    assert "sk-abcdefgh1234" not in str(settings.providers())

    registry.set_api_key("deepseek", "")
    assert registry.api_key("deepseek") == ""
    with pytest.raises(ValueError, match="http"):
        registry.create(label="bad", base_url="ftp://x")
    with pytest.raises(ValueError, match="kind"):
        registry.create(label="bad", base_url="https://x", kind="gemini")


def test_registry_refuses_to_delete_a_connection_in_use() -> None:
    registry, settings, _ = _registry()
    registry.create(label="P", base_url="https://p.example/v1", provider_id="p", api_key="k")
    settings.set("flow.models.chat", {"provider_id": "p", "model": "m"})
    with pytest.raises(ValueError, match="flow.models.chat"):
        registry.delete("p")
    settings.set("flow.models.chat", None)
    registry.delete("p")
    assert registry.get("p") is None
    assert settings.secrets.get_secret("provider.p.api_key") == ""


def test_changing_base_url_drops_the_cached_model_list() -> None:
    registry, settings, _ = _registry()
    registry.create(label="P", base_url="https://p.example/v1", provider_id="p")
    settings.set_providers([{**settings.providers()[0], "models": ["m1"]}])
    assert registry.update("p", label="Renamed").models == ("m1",)
    assert registry.update("p", base_url="https://q.example/v1").models == ()


async def test_test_connection_lists_models_then_falls_back_to_ping() -> None:
    registry, settings, state = _registry({"models": ["m1", "m2"]})
    registry.create(label="P", base_url="https://p.example/v1", provider_id="p")
    ok = await registry.test("p")
    assert ok["status"] == "ok" and ok["models"] == 2
    assert registry.require("p").models == ("m1", "m2")

    state["list_error"] = ProviderError(ERROR_NOT_FOUND, "no /models")
    settings.set("flow.models.chat", {"provider_id": "p", "model": "chosen"})
    pinged = await registry.test("p")
    assert pinged["status"] == "ok" and state["pings"] == ["chosen"]

    state["list_error"] = ProviderError(ERROR_AUTH, "API Key 无效或没有权限", 401)
    failed = await registry.test("p")
    assert failed == {**failed, "status": "error", "code": ERROR_AUTH}
    assert registry.public(registry.require("p"))["status"]["code"] == ERROR_AUTH


async def test_anthropic_connections_require_a_key() -> None:
    registry, _, _ = _registry()
    registry.create(label="Claude", base_url="https://api.anthropic.com", kind="anthropic",
                    provider_id="claude")
    result = await registry.test("claude")
    assert result["code"] == ERROR_NO_KEY


# ── 按角色路由 ───────────────────────────────────────────────────


async def test_roles_follow_chat_and_pick_up_changes_without_restart() -> None:
    registry, settings, state = _registry()
    registry.create(label="A", base_url="https://a.example/v1", provider_id="a", api_key="ka")
    registry.create(label="B", base_url="https://b.example/v1", provider_id="b", api_key="kb")
    router = ModelRouter(settings, registry)
    research = LLMAdapter(router.client_for("research"))
    assert not research.configured
    assert "离线测试占位答案" in await research.generate("q")

    settings.set("flow.models.chat", {"provider_id": "a", "model": "chat-m"})
    assert research.configured
    assert await research.generate("q1") == "chat-m:q1"  # research 跟随 chat

    settings.set("flow.models.research", {"provider_id": "b", "model": "deep-m"})
    assert await research.generate("q2") == "deep-m:q2"
    assert router.describe("graph") == "a:chat-m"

    registry.set_api_key("b", "kb-rotated")
    await research.generate("q3")
    assert state["built"][-1][:3] == ("b", "kb-rotated", "deep-m")


async def test_graph_role_passes_its_own_timeout_and_retries() -> None:
    registry, settings, state = _registry()
    registry.create(label="L", base_url="http://localhost:1234/v1", provider_id="l")
    settings.set("flow.models.graph", {"provider_id": "l", "model": "phi4"})
    router = ModelRouter(settings, registry)
    await router.client_for("graph", timeout_sec=900, max_retries=2).generate_result("x")
    assert state["built"][-1] == ("l", "", "phi4", 900, 2)


async def test_dangling_assignment_reports_missing_connection() -> None:
    registry, settings, _ = _registry()
    settings.set("flow.models.chat", {"provider_id": "gone", "model": "m"})
    client = ModelRouter(settings, registry).client_for("chat")
    assert not client.configured
    with pytest.raises(ProviderError, match="gone"):
        await client.generate_result("q", allow_mock=False)
