"""LLM 结构化抽取适配器（adapters 层）。

主 LLM 经注入的 chat client 调用（问答生成与学术实体/有向关系的结构化 JSON 抽取）；
另含 OpenAI 兼容端点的 HTTP 客户端，以及未配置模型时的离线占位实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from typing import Any, Protocol

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
from kacore.managers.token_budget import default_tokenizer
from kacore.utils.usage_ledger import current_usage, usage_stage

logger = logging.getLogger("LLMAdapter")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_usage(
    result: GenerationResult | None, system: str, prompt: str, provider_id: str,
    started: float, status: str,
) -> None:
    """两种适配器共用真实尝试计账，不保存模型输入文本。"""
    ledger = current_usage.get()
    if ledger is None:
        return
    counter = default_tokenizer()
    known = result is not None
    actual = (known and result.prompt_tokens is not None
              and result.completion_tokens is not None)
    unknown_output = (known and result.reasoning_present
                      and result.completion_tokens is None)
    ledger.calls.append({
        "call_id": f"{ledger.request_id}:{len(ledger.calls) + 1}",
        "stage": usage_stage.get(), "attempt": len(ledger.calls) + 1,
        "provider": provider_id, "model": result.model if known else "",
        "input_tokens": (result.prompt_tokens if result.prompt_tokens is not None
                         else counter.count(system + prompt)) if known else None,
        "output_tokens": None if unknown_output else (result.completion_tokens
                          if result.completion_tokens is not None
                          else counter.count(result.text)) if known else None,
        "measurement": ("unknown" if unknown_output or not known
                        else "actual" if actual else "estimated"),
        "status": status, "elapsed_ms": round((time.monotonic() - started) * 1000),
    })


class LMStudioLLMAdapter:
    """OpenAI-compatible HTTP adapter，供图谱构建及配置独立 endpoint 的研究问答使用。

    调用任意 OpenAI-compatible endpoint（LM Studio / Ollama / vLLM 等）。
    也可作为 LLMAdapter 的 chat client 注入；图谱构建 LLM 与答案生成 LLM 互不影响。
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
        result = await self.generate_result(prompt, system_prompt, allow_mock=allow_mock)
        return result.text

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        """独立 endpoint 的结构化生成；HTTP 重试逐次计账，失败消费为 unknown。"""
        try:
            import aiohttp
        except ImportError as exc:
            if not allow_mock:
                raise RuntimeError("aiohttp is required for LMStudioLLMAdapter") from exc
            logger.error("aiohttp not available; LMStudioLLMAdapter cannot call LM Studio")
            return GenerationResult(text="", status=STATUS_ERROR)

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
            result = None
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
                text = self._log_success(
                    data,
                    elapsed=elapsed,
                    prompt_tokens_fallback=estimated_prompt_tokens,
                    attempt=attempt,
                )
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                usage = data.get("usage") or {}
                reason = str(choice.get("finish_reason") or "")
                reasoning = bool(message.get("reasoning_content"))
                status = (STATUS_LENGTH if reason == "length" else
                          STATUS_CONTENT_FILTER if reason == "content_filter" else
                          STATUS_OK if text else
                          STATUS_REASONING_ONLY if reasoning else STATUS_EMPTY)
                result = GenerationResult(
                    text=text, status=status, finish_reason=reason,
                    prompt_tokens=_as_int(usage.get("prompt_tokens")),
                    completion_tokens=_as_int(usage.get("completion_tokens")),
                    reasoning_present=reasoning, provider_id="openai-compatible",
                    model=str(data.get("model") or self._model),
                )
                return result
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
            finally:
                _record_usage(result, system_prompt, prompt, "openai-compatible", t0,
                              result.status if result else STATUS_ERROR)

        if not allow_mock:
            raise RuntimeError(f"LM Studio call to {url} failed: {last_exc}") from last_exc
        logger.error("LMStudioLLMAdapter.generate failed after retries: %s", last_exc)
        return GenerationResult(text="", status=STATUS_ERROR)

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


class ChatClient(Protocol):
    """主 LLM 的最小调用契约（P2 起由模型路由按角色提供实现）。

    allow_mock=False 时失败必须抛异常或返回 status!=ok 的结果，不得返回占位文本；
    LLMAdapter 总以 allow_mock=False 调用，占位兜底只在 LLMAdapter 一层做。
    """

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult: ...


class LLMAdapter:
    """主 LLM 调用适配器（问答生成 + 结构化抽取）。

    client：注入的 chat client；None 表示尚未配置对话模型，生成退化为离线占位文本
    （allow_mock=False 时直接抛错）。
    timeout_seconds：单次调用上限。端点自身不保证有超时，挂死时整条 ask 会永远不返回——
    前端只能看到「请求超时」，后端却仍占着连接。<=0 表示不设限。
    """

    # 开了 reasoning 的模型可能只返回 reasoning_content、不给最终答案。若不识别会被当空响应；
    # 对同一模型追加「直接给答案」的指令重试一次，比直接判失败更常拿到可用结果。
    _REASONING_RETRY_SUFFIX = "\n\n请直接输出最终结论/答案本身，不要仅输出推理过程。"

    def __init__(self, client: ChatClient | None = None, timeout_seconds: int = 300) -> None:
        self._client = client
        self._timeout_seconds = max(0, int(timeout_seconds))

    @property
    def configured(self) -> bool:
        """是否已注入真实 chat client（未注入时所有生成都是离线占位）。"""
        return self._client is not None

    async def generate(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> str:
        """通用文本生成。薄委派 `generate_result()`——只取文本，不需要状态诊断的调用方用这个。"""
        result = await self.generate_result(prompt, system_prompt, allow_mock=allow_mock)
        return result.text

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        """通用文本生成，返回结构化结果（finish_reason/token 用量/reasoning 状态等）。

        供需要按状态分流决策的调用方使用（如 answer_synthesis/llm_json），而不是只靠
        「文本是否为空」判断成败——reasoning_only/length/content_filter 时文本可能为空
        也可能非空，必须看 status。allow_mock=False 时无真实响应直接抛异常，不做离线占位兜底。
        """
        result = await self._call_client_result(prompt, system_prompt)

        if not result.text or not result.text.strip():
            if not allow_mock:
                # 具体原因（未配置 / 超时 / 异常 / 空响应 / reasoning-only）已由
                # _call_client_result 记进日志；这里指路而不复述。
                raise RuntimeError(
                    "No real LLM response (未配置模型/超时/异常/空响应，"
                    "详见日志中 LLMAdapter 的上一条 ERROR/WARNING)；"
                    "mock fallback is disabled"
                )
            # 降级到离线占位答案是「回答看起来正常、内容其实是假的」，必须是 WARNING 级。
            logger.warning(
                "主 LLM 未返回内容，本次回答使用离线占位文本（非真实回答）；"
                "请在数据流「模型分配」中配置对话模型并检查上方错误日志。"
            )
            return replace(result, text=self._mock_generate(prompt).strip())

        return replace(result, text=result.text.strip())

    async def _call_context_llm(self, prompt: str, system_prompt: str = "") -> str:
        """调用主 LLM，只取纯文本（供 extract_graph 等不需要状态诊断的调用方）。"""
        return (await self._call_client_result(prompt, system_prompt)).text

    async def _call_client_result(
        self, prompt: str, system_prompt: str = ""
    ) -> GenerationResult:
        """调用注入的 chat client，返回结构化结果。

        契约：本方法不抛异常（status!=ok 即失败），但失败/空响应必须留下可诊断的日志——
        调用方拿到非 ok 状态后会据此分流（重试/降级/直接失败），日志是唯一线索。
        """
        if self._client is None:
            logger.warning("LLMAdapter 未配置对话模型，无法调用主 LLM。")
            return GenerationResult(text="", status=STATUS_EMPTY)

        started = time.monotonic()
        try:
            result = await self._await_client(prompt, system_prompt)
            if not result.text and result.reasoning_present:
                retry_system = (system_prompt or "") + self._REASONING_RETRY_SUFFIX
                result = await self._await_client(prompt, retry_system)
            if result.text:
                logger.info(
                    "主 LLM 调用完成 耗时 %.1fs prompt=%d 字符 response=%d 字符",
                    time.monotonic() - started,
                    len(prompt),
                    len(result.text),
                )
                return result
        except TimeoutError:
            logger.error(
                "主 LLM 调用超时（%ss，prompt %d 字符）：本次回答放弃等待。"
                "请检查模型端点的可达性与并发限制，或调大 ask.llm_timeout_seconds。",
                self._timeout_seconds,
                len(prompt),
            )
            return GenerationResult(text="", status=STATUS_TIMEOUT)
        except Exception as e:
            logger.error(
                "主 LLM 调用失败（耗时 %.1fs）：%s: %s",
                time.monotonic() - started,
                type(e).__name__,
                e,
                exc_info=True,
            )
            return GenerationResult(text="", status=STATUS_ERROR)

        if result.status == STATUS_REASONING_ONLY or result.reasoning_present:
            logger.warning(
                "主 LLM 连续两次只有 reasoning_content、未给出最终答案"
                "（耗时 %.1fs，prompt %d 字符，finish_reason=%s）。",
                time.monotonic() - started,
                len(prompt),
                result.finish_reason or "unknown",
            )
            return replace(result, status=STATUS_REASONING_ONLY)
        logger.warning(
            "主 LLM 返回空响应（耗时 %.1fs，prompt %d 字符，status=%s）："
            "模型可能被限流或输出被过滤。",
            time.monotonic() - started,
            len(prompt),
            result.status,
        )
        return result

    async def _await_client(self, prompt: str, system_prompt: str) -> GenerationResult:
        """给一次 client 调用套上超时闸；<=0 表示不限。"""
        assert self._client is not None
        call = self._client.generate_result(prompt, system_prompt, allow_mock=False)
        if self._timeout_seconds <= 0:
            return await call
        return await asyncio.wait_for(call, timeout=self._timeout_seconds)

    def _mock_generate(self, prompt: str) -> str:
        """离线占位：从 prompt 中提取关键词，构造简单示例答案。"""
        lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
        question = next((ln for ln in lines if ln.startswith("问题")), lines[0] if lines else "")
        return (
            f"根据知识库中的相关资料，对于「{question[:60]}」的问答如下：\n\n"
            "这是一个离线测试占位答案。请在数据流「模型分配」中配置对话模型以获得真实回答。"
        )

    async def extract_graph(
        self, text: str, entity_types: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """从给定的文本中，根据指定的 entity_types 提取结构化的实体和关系。

        返回格式：
        {
            "entities": [{"name": "Transformer", "type": "Method/Algorithm",
                          "description": "..."}],
            "relations": [{"src": "Transformer", "dst": "Attention",
                           "relation": "uses", "description": "...",
                           "weight": 1.0}]
        }
        """
        types_str = ", ".join(entity_types)
        system_prompt = f"""You are a brilliant scientific researcher and knowledge graph builder.
Your task is to extract unique, high-quality entities and relationships
from the provided scientific text.

CRITICAL CONSTRAINTS:
1. Every extracted entity's `type` field MUST be exactly one of the
following categories: [{types_str}]. Do NOT use any other categories.
2. Output your response as a single, valid JSON object matching this schema:
{{
    "entities": [
        {{
            "name": "Entity Name",
            "type": "One of the configured categories",
            "description": "Clean, informative academic description"
        }}
    ],
    "relations": [
        {{
            "src": "Exact Name of Src Entity",
            "dst": "Exact Name of Dst Entity",
            "relation": "Relationship word (lower_case)",
            "description": "How they are related",
            "weight": 1.0
        }}
    ]
}}
3. Do NOT wrap the JSON in Markdown backticks (e.g. ```json) in your raw response.
Output ONLY the raw JSON string.
"""

        user_prompt = (
            f"Please analyze the following academic text and extract the knowledge graph:\n\n{text}"
        )

        raw_response = await self._call_context_llm(user_prompt, system_prompt)

        # 4) 离线测试桩回退
        if not raw_response or not raw_response.strip():
            logger.info("[Offline Stub] Using mock LLM extraction stub.")
            raw_response = self._mock_llm_extraction(text, entity_types)

        # 5) 清洗并解析 JSON
        return self._clean_and_parse_json(raw_response)

    def _mock_llm_extraction(self, text: str, entity_types: list[str]) -> str:
        """纯离线高保真测试桩，从文本中发现专有名词生成结构化学术图谱。"""
        # 简单使用正则表达式发现英文首字母大写的科学名词（模拟学术概念）
        words = re.findall(r"\b[A-Z][a-zA-Z0-9-]+\b", text)
        unique_words = sorted(list(set(words)))

        entities = []
        relations = []

        # 默认回退的第一个类别
        default_type = entity_types[0] if entity_types else "Method/Algorithm"

        # 如果发现专有名词，构造实体
        for i, word in enumerate(unique_words[:4]):  # 限制数量
            entities.append(
                {
                    "name": word,
                    "type": default_type,
                    "description": f"Extracted academic concept representing {word}.",
                }
            )

        # 构造链式有向关系
        for i in range(len(entities) - 1):
            relations.append(
                {
                    "src": entities[i]["name"],
                    "dst": entities[i + 1]["name"],
                    "relation": "relates_to",
                    "description": (
                        f"{entities[i]['name']} is connected to {entities[i + 1]['name']}."
                    ),
                    "weight": 1.0,
                }
            )

        result = {"entities": entities, "relations": relations}
        return json.dumps(result)

    def _clean_and_parse_json(self, raw_str: str) -> dict[str, list[dict[str, Any]]]:
        """清洗大模型可能包含的 Markdown ```json 包裹并安全解析为字典。"""
        cleaned = raw_str.strip()
        # 移除 markdown json 标记
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "entities" in parsed:
                return parsed
        except Exception as e:
            logger.error(f"Failed to parse LLM JSON response: {e}. Raw: {raw_str}")

        return {"entities": [], "relations": []}
