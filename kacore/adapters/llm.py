"""LLM 适配器（adapters 层）——框架中立。

本模块只定义**中立契约**与**不依赖任何外部框架**的实现：

- ``LLMAdapter``：核心唯一依赖的 LLM 契约（Protocol）。业务层（api / pipelines /
  conversation / lightrag）一律按此接口标注与调用，不认识任何具体壳。
- ``LMStudioLLMAdapter``：OpenAI-compatible HTTP 实现（LM Studio / Ollama / vLLM…）。
- ``OfflineStubLLMAdapter``：未配置主 LLM 时的中立离线占位实现。

壳专属实现不在这里：AstrBot 的 provider 适配器归插件壳所有
（``apps/plugin/ka_plugin/adapters/llm.py`` 的 ``AstrBotLLMAdapter``），由壳在组合根显式注入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("LLMAdapter")


class LLMAdapter(Protocol):
    """核心依赖的 LLM 契约：只要求 ``generate``。

    ``chat_stream``（流式 + tool-calling）是**可选能力**，调用方用 ``hasattr`` 探测后
    再使用（见 ``kacore/conversation/service.py``），因此不写入本必需契约。
    """

    async def generate(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> str:
        """生成文本。``allow_mock=False`` 时不得返回占位内容，失败须抛错。"""
        ...


class LMStudioLLMAdapter:
    """OpenAI-compatible HTTP 实现（满足 ``LLMAdapter`` 契约，另提供 ``chat_stream``）。

    调用任意 OpenAI-compatible endpoint（LM Studio / Ollama / vLLM 等）。核心自用：
    独立版主 LLM、LightRAG 图谱构建 LLM、Deep Thinking 子 LLM 都可各自配置，互不影响。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout_seconds: int = 900,
        max_retries: int = 2,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    async def generate(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> str:
        try:
            import aiohttp
        except ImportError as exc:
            if not allow_mock:
                raise RuntimeError("aiohttp is required for LMStudioLLMAdapter") from exc
            logger.error("aiohttp not available; LMStudioLLMAdapter cannot call LM Studio")
            return ""

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {"model": self._model, "messages": messages, "temperature": 0.1}
        url = f"{self._base_url}/chat/completions"

        input_chars = sum(len(m["content"]) for m in messages)
        estimated_prompt_tokens = input_chars // 4
        attempts = self._max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            t0 = time.monotonic()
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                elapsed = time.monotonic() - t0
                return self._log_success(
                    data,
                    elapsed=elapsed,
                    prompt_tokens_fallback=estimated_prompt_tokens,
                    attempt=attempt,
                )
            except Exception as exc:
                last_exc = exc
                elapsed = time.monotonic() - t0
                logger.warning(
                    "LMStudio call failed attempt=%d/%d elapsed=%.1fs timeout=%ss "
                    "model=%s error=%s",
                    attempt,
                    attempts,
                    elapsed,
                    self._timeout_seconds,
                    self._model,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(self._retry_backoff_seconds * attempt)

        if not allow_mock:
            raise RuntimeError(f"LM Studio call to {url} failed: {last_exc}") from last_exc
        logger.error("LMStudioLLMAdapter.generate failed after retries: %s", last_exc)
        return ""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ):
        """OpenAI-compatible 流式对话（支持 tool-calling），供对话 Agent 使用。

        逐段 yield 事件：``{"type": "delta", "text": str}``（正文增量）；
        流结束后 yield ``{"type": "final", "content": str, "tool_calls": [
        {"id", "name", "arguments"}], "finish_reason": str}``。HTTP/解析失败抛
        RuntimeError（调用方负责向用户显式报错，不静默降级）。
        """
        import aiohttp

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        url = f"{self._base_url}/chat/completions"

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = ""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choice = (chunk.get("choices") or [{}])[0]
                        finish_reason = str(choice.get("finish_reason") or finish_reason)
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            yield {"type": "delta", "text": text}
                        for tc in delta.get("tool_calls") or []:
                            index = int(tc.get("index") or 0)
                            slot = tool_calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = str(tc["id"])
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = str(fn["name"])
                            if fn.get("arguments"):
                                slot["arguments"] += str(fn["arguments"])
        except Exception as exc:
            raise RuntimeError(f"LLM chat stream to {url} failed: {exc}") from exc

        yield {
            "type": "final",
            "content": "".join(content_parts),
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
            "finish_reason": finish_reason,
        }

    def _log_success(
        self,
        data: dict[str, Any],
        *,
        elapsed: float,
        prompt_tokens_fallback: int,
        attempt: int,
    ) -> str:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "").strip()
        finish_reason = str(choice.get("finish_reason") or "")

        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or prompt_tokens_fallback
        completion_tokens = usage.get("completion_tokens") or max(1, len(content) // 4)
        tps = completion_tokens / elapsed if elapsed > 0 else 0

        logger.debug(
            "LMStudio | prompt=%d tok completion=%d tok elapsed=%.1fs "
            "gen_speed=%.1f t/s retry=%d finish=%s model=%s",
            prompt_tokens,
            completion_tokens,
            elapsed,
            tps,
            attempt - 1,
            finish_reason or "unknown",
            self._model,
        )

        if tps < 8 and completion_tokens > 10:
            logger.warning(
                "LMStudio 生成速度偏低 %.1f t/s（prompt=%d tok）"
                "——长 prompt 会显著降低推理速度，可减小 max_doc_chars 缩短输入",
                tps,
                prompt_tokens,
            )

        return content


class OfflineStubLLMAdapter:
    """未配置主 LLM 时的中立离线占位实现（满足 ``LLMAdapter`` 契约）。

    组合根在两壳都未注入可用 LLM 时装配它：自然语言问答返回可读的配置指引占位，
    而非静默失败。``allow_mock=False``（改写、JSON 合成等不接受占位的通路）显式抛错，
    由调用方降级——保证占位内容**永不**混入证据或结构化结果。
    """

    async def generate(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> str:
        if not allow_mock:
            raise RuntimeError("No LLM is configured; offline stub cannot serve this call")
        lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
        question = next((ln for ln in lines if ln.startswith("问题")), lines[0] if lines else "")
        return (
            f"根据知识库中的相关资料，对于「{question[:60]}」的问答如下：\n\n"
            "这是一个离线占位答案。请先在设置中配置主模型（OpenAI 兼容端点）以获得真实回答。"
        )


__all__ = ["LLMAdapter", "LMStudioLLMAdapter", "OfflineStubLLMAdapter"]


