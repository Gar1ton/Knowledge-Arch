"""OpenAI 兼容端点的连接客户端（OpenAI / DeepSeek / DashScope / Moonshot / 智谱 / SiliconFlow /
OpenRouter / Ollama / LM Studio / vLLM …）。

只用 `POST {base_url}/chat/completions` 与 `GET {base_url}/models` 两个接口；网络错误与 5xx
有界重试，4xx（密钥 / 地址 / 模型错误）不重试。每次真实尝试都记一次用量账。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from kacore.domain.llm_generation import (
    STATUS_CONTENT_FILTER,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_LENGTH,
    STATUS_OK,
    STATUS_REASONING_ONLY,
    GenerationResult,
)
from kacore.providers.base import (
    ERROR_AUTH,
    ERROR_BAD_RESPONSE,
    ERROR_NOT_FOUND,
    ERROR_RATE_LIMITED,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_UNREACHABLE,
    ProviderClient,
    ProviderError,
)
from kacore.providers.usage import record_llm_usage

logger = logging.getLogger("OpenAICompatChat")

PROVIDER_LABEL = "openai-compatible"
# 生成速度低于此值时提示：长 prompt 会显著降低本地模型推理速度。
_SLOW_TOKENS_PER_SECOND = 8


def classify_http_status(status: int, body: str = "") -> ProviderError:
    """把 HTTP 状态码翻译成带可读提示的 ProviderError（正文只截取一小段用于诊断）。"""
    snippet = body.strip().replace("\n", " ")[:200]
    if status in (401, 403):
        return ProviderError(ERROR_AUTH, "Invalid API key or no permission", status)
    if status == 404:
        return ProviderError(ERROR_NOT_FOUND, "Address or model not found (HTTP 404)", status)
    if status == 429:
        return ProviderError(ERROR_RATE_LIMITED, (
            "Too many requests or quota exhausted (HTTP 429)"
        ), status)
    if status >= 500:
        return ProviderError(ERROR_SERVER, f"Server error (HTTP {status})", status)
    suffix = f": {snippet}" if snippet else ""
    return ProviderError(ERROR_BAD_RESPONSE, f"Request rejected (HTTP {status}){suffix}", status)


def classify_exception(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return ProviderError(ERROR_TIMEOUT, "Request timed out")
    if isinstance(exc, aiohttp.ClientConnectorError):
        return ProviderError(ERROR_UNREACHABLE, "Connection refused or address unreachable")
    if isinstance(exc, aiohttp.ClientError):
        return ProviderError(ERROR_UNREACHABLE, f"Network error: {type(exc).__name__}")
    return ProviderError(ERROR_BAD_RESPONSE, f"Response unavailable: {type(exc).__name__}")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class OpenAICompatChat(ProviderClient):
    """一个 OpenAI 兼容连接上的一个模型。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout_seconds: int = 120,
        max_retries: int = 2,
        retry_backoff_seconds: float = 2.0,
        provider_id: str = PROVIDER_LABEL,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._provider_id = provider_id

    @property
    def model(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def generate(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> str:
        return (await self.generate_result(prompt, system_prompt, allow_mock=allow_mock)).text

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        """结构化生成；可重试错误逐次计账，失败消费记为 unknown。"""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self._model, "messages": messages, "temperature": 0.1}
        url = f"{self._base_url}/chat/completions"
        attempts = self._max_retries + 1
        last_error: ProviderError | None = None

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            result: GenerationResult | None = None
            try:
                data = await self._post_json(url, payload)
                result = self._build_result(data, started=started, attempt=attempt)
                return result
            except Exception as exc:
                last_error = classify_exception(exc)
                logger.warning(
                    "OpenAI-compatible call failed attempt=%d/%d elapsed=%.1fs model=%s code=%s",
                    attempt,
                    attempts,
                    time.monotonic() - started,
                    self._model,
                    last_error.code,
                )
                retryable = last_error.code in {
                    ERROR_SERVER,
                    ERROR_UNREACHABLE,
                    ERROR_TIMEOUT,
                    ERROR_RATE_LIMITED,
                }
                if not retryable:
                    break
                if attempt < attempts:
                    await asyncio.sleep(self._retry_backoff_seconds * attempt)
            finally:
                record_llm_usage(
                    result, system_prompt, prompt, self._provider_id, started,
                    result.status if result else STATUS_ERROR,
                )

        assert last_error is not None
        if not allow_mock:
            raise last_error
        logger.error("OpenAI-compatible generate failed: %s", last_error.message)
        return GenerationResult(text="", status=STATUS_ERROR, provider_id=self._provider_id)

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise classify_http_status(resp.status, await resp.text())
                try:
                    data = await resp.json(content_type=None)
                except ValueError as exc:
                    raise ProviderError(ERROR_BAD_RESPONSE, (
                        "The response is not valid JSON"
                    )) from exc
        if not isinstance(data, dict):
            raise ProviderError(ERROR_BAD_RESPONSE, "Unexpected response shape")
        return data

    def _build_result(
        self, data: dict[str, Any], *, started: float, attempt: int
    ) -> GenerationResult:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = str(message.get("content") or "").strip()
        usage = data.get("usage") or {}
        reason = str(choice.get("finish_reason") or "")
        reasoning = bool(message.get("reasoning_content"))
        status = (
            STATUS_LENGTH if reason == "length"
            else STATUS_CONTENT_FILTER if reason == "content_filter"
            else STATUS_OK if text
            else STATUS_REASONING_ONLY if reasoning
            else STATUS_EMPTY
        )
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))
        self._log_speed(completion_tokens, len(text), time.monotonic() - started, attempt)
        return GenerationResult(
            text=text,
            status=status,
            finish_reason=reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_present=reasoning,
            provider_id=self._provider_id,
            model=str(data.get("model") or self._model),
        )

    def _log_speed(
        self, completion_tokens: int | None, chars: int, elapsed: float, attempt: int
    ) -> None:
        tokens = completion_tokens or max(1, chars // 4)
        tps = tokens / elapsed if elapsed > 0 else 0
        logger.debug(
            "OpenAI-compatible | completion=%d tok elapsed=%.1fs speed=%.1f t/s retry=%d model=%s",
            tokens, elapsed, tps, attempt - 1, self._model,
        )
        if tps < _SLOW_TOKENS_PER_SECOND and tokens > 10:
            logger.warning(
                "Model generation speed is low at %.1f t/s; long prompts slow local inference "
                "noticeably, consider lowering max_doc_chars",
                tps,
            )

    async def list_models(self) -> list[str]:
        timeout = aiohttp.ClientTimeout(total=min(self._timeout_seconds, 30))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._base_url}/models", headers=self._headers()) as resp:
                    if resp.status != 200:
                        raise classify_http_status(resp.status, await resp.text())
                    data = await resp.json(content_type=None)
        except Exception as exc:
            raise classify_exception(exc) from None
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(ERROR_BAD_RESPONSE, "Unexpected model list shape")
        return sorted(
            {str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")}
        )

    async def ping(self, model: str) -> None:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        try:
            await self._post_json(f"{self._base_url}/chat/completions", payload)
        except Exception as exc:
            raise classify_exception(exc) from None


__all__ = ["OpenAICompatChat", "classify_exception", "classify_http_status"]
