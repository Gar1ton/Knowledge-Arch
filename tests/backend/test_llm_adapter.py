"""LLMAdapter 与注入 chat client 的契约测试；OpenAI 兼容端点的 HTTP 客户端测试。"""

from __future__ import annotations

import asyncio
from typing import Any

from kacore.adapters.llm import LLMAdapter
from kacore.domain.llm_generation import (
    STATUS_CONTENT_FILTER,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_LENGTH,
    STATUS_OK,
    STATUS_REASONING_ONLY,
    STATUS_TIMEOUT,
    GenerationResult,
)


async def test_independent_endpoint_supports_json_contract_and_counts_http_retries():
    import json

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from kacore.pipelines.llm_json import llm_json_call
    from kacore.providers.openai_compat import OpenAICompatChat
    from kacore.utils.usage_ledger import current_usage, usage_request

    attempts = 0

    async def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return web.json_response({"error": "busy"}, status=503)
        return web.json_response({
            "model": "test-model",
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 2},
        })

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    server = TestServer(app)
    await server.start_server()

    @usage_request
    async def run():
        adapter = OpenAICompatChat(str(server.make_url("/v1")), "test-model",
                                     retry_backoff_seconds=0, max_retries=1)
        parsed, calls, tokens = await llm_json_call(adapter, "q", "sys", json.loads, 0)
        return parsed, calls, tokens, current_usage.get().summary()

    try:
        parsed, calls, tokens, usage = await run()
        assert parsed == {"ok": True} and calls == 1 and tokens == 42
        assert usage["call_count"] == 2 and usage["unknown_calls"] == 1
        assert usage["calls"][1]["model"] == "test-model"
        assert usage["calls"][1]["measurement"] == "actual"
    finally:
        await server.close()


# ── 注入 chat client 的契约 ──────────────────────────────────────


class _Client:
    """按序返回预置结果的 chat client 替身；记录每次调用的 (prompt, system, allow_mock)。"""

    def __init__(self, *results: GenerationResult | Exception) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str, bool]] = []

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        self.calls.append((prompt, system_prompt, allow_mock))
        item = self._results.pop(0) if self._results else GenerationResult("", STATUS_EMPTY)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(text: str, **kwargs: Any) -> GenerationResult:
    return GenerationResult(text=text, status=STATUS_OK, **kwargs)


async def test_generate_delegates_to_client_and_never_lets_it_mock() -> None:
    client = _Client(_ok("  answer  ", finish_reason="stop", prompt_tokens=3, completion_tokens=1))
    adapter = LLMAdapter(client)

    result = await adapter.generate_result("q", "sys")

    assert result.text == "answer" and result.status == STATUS_OK
    assert result.finish_reason == "stop" and result.prompt_tokens == 3
    assert client.calls == [("q", "sys", False)]
    assert adapter.configured is True


async def test_unconfigured_adapter_uses_offline_placeholder_with_warning(caplog: Any) -> None:
    adapter = LLMAdapter()
    with caplog.at_level("WARNING", logger="LLMAdapter"):
        text = await adapter.generate("问题：什么是 RAG？")
    assert "离线测试占位答案" in text and "模型分配" in text
    assert adapter.configured is False
    assert any("离线占位" in r.getMessage() for r in caplog.records)


async def test_unconfigured_adapter_raises_when_mock_is_disabled() -> None:
    try:
        await LLMAdapter().generate("q", allow_mock=False)
    except RuntimeError as exc:
        assert "mock fallback is disabled" in str(exc)
    else:  # pragma: no cover - 契约要求抛错
        raise AssertionError("expected RuntimeError")


async def test_client_exception_is_logged_with_traceback(caplog: Any) -> None:
    adapter = LLMAdapter(_Client(ConnectionError("refused")))
    with caplog.at_level("ERROR", logger="LLMAdapter"):
        result = await adapter._call_client_result("q")
    assert result.status == STATUS_ERROR
    record = next(r for r in caplog.records if "主 LLM 调用失败" in r.getMessage())
    assert record.exc_info is not None


async def test_main_llm_call_is_time_boxed(caplog: Any) -> None:
    class _Slow:
        async def generate_result(self, prompt: str, system_prompt: str = "", **_: Any):
            await asyncio.sleep(5)
            return _ok("late")

    adapter = LLMAdapter(_Slow(), timeout_seconds=1)
    adapter._timeout_seconds = 0.05  # type: ignore[assignment]
    with caplog.at_level("ERROR", logger="LLMAdapter"):
        result = await adapter.generate_result("q", allow_mock=True)
    assert "离线测试占位答案" in result.text
    assert any("调用超时" in r.getMessage() for r in caplog.records)
    raw = await adapter._call_client_result("q")
    assert raw.status == STATUS_TIMEOUT


async def test_llm_timeout_zero_keeps_unbounded_wait() -> None:
    class _Delayed:
        async def generate_result(self, prompt: str, system_prompt: str = "", **_: Any):
            await asyncio.sleep(0.05)
            return _ok("done")

    adapter = LLMAdapter(_Delayed(), timeout_seconds=0)
    assert await adapter.generate("q") == "done"


async def test_reasoning_only_retries_once_with_direct_answer_instruction() -> None:
    client = _Client(
        GenerationResult("", STATUS_REASONING_ONLY, reasoning_present=True),
        _ok("final"),
    )
    result = await LLMAdapter(client).generate_result("q", "sys")
    assert result.text == "final"
    assert len(client.calls) == 2
    assert client.calls[1][1].startswith("sys") and "最终结论" in client.calls[1][1]


async def test_reasoning_only_twice_reports_reasoning_only_status(caplog: Any) -> None:
    client = _Client(
        GenerationResult("", STATUS_REASONING_ONLY, reasoning_present=True),
        GenerationResult("", STATUS_EMPTY, reasoning_present=True, finish_reason="stop"),
    )
    adapter = LLMAdapter(client)
    with caplog.at_level("WARNING", logger="LLMAdapter"):
        raw = await adapter._call_client_result("q")
    assert raw.status == STATUS_REASONING_ONLY
    assert any("reasoning_content" in r.getMessage() for r in caplog.records)
    # 占位兜底保留状态，调用方据此提示「本次生成不寻常」。
    client2 = _Client(
        GenerationResult("", STATUS_REASONING_ONLY, reasoning_present=True),
        GenerationResult("", STATUS_REASONING_ONLY, reasoning_present=True),
    )
    mocked = await LLMAdapter(client2).generate_result("q")
    assert mocked.status == STATUS_REASONING_ONLY and "离线测试占位答案" in mocked.text


async def test_true_empty_response_is_not_retried() -> None:
    client = _Client(GenerationResult("", STATUS_EMPTY))
    raw = await LLMAdapter(client)._call_client_result("q")
    assert raw.status == STATUS_EMPTY and len(client.calls) == 1


async def test_length_and_content_filter_status_pass_through() -> None:
    for status in (STATUS_LENGTH, STATUS_CONTENT_FILTER):
        client = _Client(GenerationResult("partial", status, finish_reason=status))
        result = await LLMAdapter(client).generate_result("q")
        assert result.status == status and result.text == "partial"


async def test_missing_usage_stays_none_not_zero() -> None:
    result = await LLMAdapter(_Client(_ok("a"))).generate_result("q")
    assert result.prompt_tokens is None and result.completion_tokens is None
    client = _Client(_ok("a", prompt_tokens=0, completion_tokens=0))
    zero = await LLMAdapter(client).generate_result("q")
    assert zero.prompt_tokens == 0 and zero.completion_tokens == 0


async def test_extract_graph_uses_client_and_parses_json() -> None:
    payload = (
        '{"entities": [{"name": "RAG", "type": "Method", "description": "d"}], '
        '"relations": []}'
    )
    client = _Client(_ok(f"```json\n{payload}\n```"))
    graph = await LLMAdapter(client).extract_graph("text", ["Method"])
    assert graph["entities"][0]["name"] == "RAG"
    assert "Method" in client.calls[0][1]


async def test_extract_graph_falls_back_to_offline_stub_without_client() -> None:
    graph = await LLMAdapter().extract_graph("Transformer uses Attention heavily", ["Method"])
    assert {e["name"] for e in graph["entities"]} >= {"Transformer", "Attention"}
