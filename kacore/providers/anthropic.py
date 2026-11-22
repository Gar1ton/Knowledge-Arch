"""Anthropic（Claude）连接客户端，经官方 `anthropic` SDK 调用 Messages API。

- 不发送 temperature / top_p / top_k（当前 API 版本对所有模型都会拒绝）。
- 缺省不设置 thinking：当前模型缺省即自适应思考；只取 text 块作为答案，thinking 块只用于判断
  「是否只有推理没有答案」。提问关闭了「模型思考」时按型号追加关闭参数（见 `thinking.py`），
  被拒（400）就去掉参数重试一次。
- 对支持的模型默认开启服务端拒答回退（`fallbacks: "default"`）：安全分类器拒答时由 API 在同一
  请求内换模型重跑；只在官方 API 地址上启用（代理 / 第三方网关不一定支持该 beta）。
- `stop_reason == "refusal"` 映射为 content_filter；`max_tokens` 映射为 length。
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import anthropic

from kacore.domain.llm_generation import (
    STATUS_CONTENT_FILTER,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_LENGTH,
    STATUS_OK,
    STATUS_REASONING_ONLY,
    GenerationResult,
)
from kacore.domain.tool_calls import (
    AssistantTurn,
    ToolCallRequest,
    ToolSpec,
    ToolTurn,
    Turn,
    UserTurn,
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
from kacore.providers.thinking import anthropic_thinking_off, thinking_disabled
from kacore.providers.usage import record_llm_usage

logger = logging.getLogger("AnthropicChat")

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-opus-5-5"
# 预设列表只放当前代；用户可在「获取模型列表」后选任何模型。
PRESET_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
# 非流式请求的输出上限：足够长答案，又不触发 SDK 的长请求超时保护。
_MAX_TOKENS = 16000
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)


def classify_anthropic_error(exc: BaseException) -> ProviderError:
    """SDK 异常按「最具体在前」翻译；超时是连接错误的子类，必须先判。"""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderError(ERROR_AUTH, "Invalid API key or no permission", exc.status_code)
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(ERROR_NOT_FOUND, "Address or model not found (HTTP 404)", 404)
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(ERROR_RATE_LIMITED, (
            "Too many requests or quota exhausted (HTTP 429)"
        ), 429)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderError(
                ERROR_SERVER, f"Server error (HTTP {exc.status_code})", exc.status_code
            )
        return ProviderError(
            ERROR_BAD_RESPONSE, f"Request rejected (HTTP {exc.status_code})", exc.status_code
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError(ERROR_TIMEOUT, "Request timed out")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(ERROR_UNREACHABLE, "Connection refused or address unreachable")
    return ProviderError(ERROR_BAD_RESPONSE, f"Response unavailable: {type(exc).__name__}")


def _anthropic_messages(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    """供应商中立的轮次 → Messages API 的 messages；相邻的工具结果合进同一条 user 消息，
    紧跟工具结果的用户文字（distributor 最后一轮的提示）也并进这条消息、排在工具结果之后。"""
    out: list[dict[str, Any]] = []
    for turn in turns:
        if isinstance(turn, UserTurn):
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append({"type": "text", "text": turn.text})
            else:
                out.append({"role": "user", "content": turn.text})
        elif isinstance(turn, AssistantTurn):
            content: list[Any] = list(turn.raw) if isinstance(turn.raw, list) else [
                *([{"type": "text", "text": turn.text}] if turn.text else []),
                *({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                  for c in turn.calls),
            ]
            out.append({"role": "assistant", "content": content})
        else:
            block = {"type": "tool_result", "tool_use_id": turn.call_id, "content": turn.text,
                     **({"is_error": True} if turn.is_error else {})}
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _turns_text(turns: Sequence[Turn]) -> str:
    """用量估算用的输入文本（响应没报 token 数时按它计）。"""
    return "\n".join(getattr(t, "text", "") for t in turns)


class AnthropicChat(ProviderClient):
    """一个 Anthropic 连接上的一个模型。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        timeout_seconds: int = 120,
        max_retries: int = 2,
        provider_id: str = "anthropic",
        client: Any | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model
        self._provider_id = provider_id
        # client 只给测试注入；生产由本类按连接参数构造。
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self._base_url,
            timeout=float(max(1, int(timeout_seconds))),
            max_retries=max(0, int(max_retries)),
        )

    @property
    def model(self) -> str:
        return self._model

    def _uses_fallbacks(self, model: str) -> bool:
        return model in _FALLBACK_MODELS and self._base_url == DEFAULT_BASE_URL

    async def _create(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if self._uses_fallbacks(model):
            return await self._client.beta.messages.create(
                model=model,
                messages=messages,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
                **kwargs,
            )
        return await self._client.messages.create(model=model, messages=messages, **kwargs)

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {"max_tokens": _MAX_TOKENS}
        if system_prompt:
            kwargs["system"] = system_prompt
        thinking_off = anthropic_thinking_off(self._model) if thinking_disabled() else {}
        if thinking_off:
            try:
                return await self._generate_once(
                    prompt, system_prompt, {**kwargs, **thinking_off}, allow_mock=False
                )
            except ProviderError as error:
                if error.status != 400:
                    if not allow_mock:
                        raise
                    return GenerationResult(
                        text="", status=STATUS_ERROR, provider_id=self._provider_id
                    )
                logger.warning(
                    "Anthropic rejected the thinking-off parameters model=%s; retrying without",
                    self._model,
                )
        return await self._generate_once(prompt, system_prompt, kwargs, allow_mock=allow_mock)

    async def generate_with_tools(
        self, turns: Sequence[Turn], tools: Sequence[ToolSpec], system_prompt: str = ""
    ) -> ToolTurn:
        """Messages API 的 tools / tool_use / tool_result。助手内容块（含思考块与签名）原样留在 raw
        里回放：带工具的多轮对话要求把思考块交回。"""
        kwargs: dict[str, Any] = {
            "max_tokens": _MAX_TOKENS,
            "tools": [{"name": t.name, "description": t.description,
                       "input_schema": t.input_schema} for t in tools],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        messages = _anthropic_messages(turns)
        thinking_off = anthropic_thinking_off(self._model) if thinking_disabled() else {}
        started = time.monotonic()
        result: GenerationResult | None = None
        try:
            try:
                response = await self._create(self._model, messages, **kwargs, **thinking_off)
            except Exception as exc:
                if not thinking_off or classify_anthropic_error(exc).status != 400:
                    raise
                response = await self._create(self._model, messages, **kwargs)
            result = self._build_result(response)
            blocks = list(getattr(response, "content", None) or [])
            calls = tuple(
                ToolCallRequest(str(b.id), str(b.name), dict(b.input or {}))
                for b in blocks if getattr(b, "type", "") == "tool_use"
            )
            if calls:
                result = replace(result, status=STATUS_OK)
            raw = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
                   for b in blocks]
            return ToolTurn(result, calls, raw)
        except Exception as exc:
            error = classify_anthropic_error(exc)
            logger.warning("Anthropic tool call failed model=%s code=%s", self._model, error.code)
            raise error from None
        finally:
            record_llm_usage(result, system_prompt, _turns_text(turns), self._provider_id,
                             started, result.status if result else STATUS_ERROR)

    async def describe_image(
        self,
        prompt: str,
        image: bytes,
        system_prompt: str = "",
        *,
        max_tokens: int = 800,
        media_type: str = "image/png",
    ) -> GenerationResult:
        """图片块 + 文字块。识图是后台任务：能关思考就关，被拒（400）则去掉重试一次。"""
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.b64encode(image).decode("ascii"),
            }},
            {"type": "text", "text": prompt},
        ]
        kwargs: dict[str, Any] = {"max_tokens": max_tokens}
        if system_prompt:
            kwargs["system"] = system_prompt
        thinking_off = anthropic_thinking_off(self._model)
        if thinking_off:
            try:
                return await self._generate_once(
                    prompt, system_prompt, {**kwargs, **thinking_off}, allow_mock=False,
                    content=content,
                )
            except ProviderError as error:
                if error.status != 400:
                    raise
        return await self._generate_once(
            prompt, system_prompt, kwargs, allow_mock=False, content=content
        )

    async def _generate_once(
        self,
        prompt: str,
        system_prompt: str,
        kwargs: dict[str, Any],
        *,
        allow_mock: bool,
        content: list[dict[str, Any]] | None = None,
    ) -> GenerationResult:
        started = time.monotonic()
        result: GenerationResult | None = None
        try:
            response = await self._create(
                self._model, [{"role": "user", "content": content or prompt}], **kwargs
            )
            result = self._build_result(response)
            return result
        except Exception as exc:
            error = classify_anthropic_error(exc)
            logger.warning(
                "Anthropic call failed model=%s code=%s elapsed=%.1fs",
                self._model, error.code, time.monotonic() - started,
            )
            if not allow_mock:
                raise error from None
            return GenerationResult(text="", status=STATUS_ERROR, provider_id=self._provider_id)
        finally:
            record_llm_usage(
                result, system_prompt, prompt, self._provider_id, started,
                result.status if result else STATUS_ERROR,
            )

    def _build_result(self, response: Any) -> GenerationResult:
        texts: list[str] = []
        reasoning = False
        for block in getattr(response, "content", None) or []:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                texts.append(str(getattr(block, "text", "") or ""))
            elif block_type in {"thinking", "redacted_thinking"}:
                reasoning = True
        text = "".join(texts).strip()
        stop_reason = str(getattr(response, "stop_reason", "") or "")
        if stop_reason == "refusal":
            status = STATUS_CONTENT_FILTER
        elif stop_reason == "max_tokens":
            status = STATUS_LENGTH
        elif text:
            status = STATUS_OK
        elif reasoning:
            status = STATUS_REASONING_ONLY
        else:
            status = STATUS_EMPTY
        usage = getattr(response, "usage", None)
        return GenerationResult(
            text=text,
            status=status,
            finish_reason=stop_reason,
            prompt_tokens=getattr(usage, "input_tokens", None),
            completion_tokens=getattr(usage, "output_tokens", None),
            reasoning_present=reasoning and not text,
            provider_id=self._provider_id,
            model=str(getattr(response, "model", "") or self._model),
        )

    async def list_models(self) -> list[str]:
        try:
            return sorted({model.id async for model in self._client.models.list()})
        except Exception as exc:
            raise classify_anthropic_error(exc) from None

    async def ping(self, model: str) -> None:
        try:
            await self._create(model, [{"role": "user", "content": "ping"}], max_tokens=1)
        except Exception as exc:
            raise classify_anthropic_error(exc) from None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "PRESET_MODELS",
    "AnthropicChat",
    "classify_anthropic_error",
]
