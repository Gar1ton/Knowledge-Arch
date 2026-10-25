"""LLM 结构化抽取适配器（adapters 层）。

主 LLM 经注入的 chat client（由 ModelRouter 按角色提供）调用：问答生成与学术实体 / 有向关系的
结构化 JSON 抽取；未配置模型时提供离线占位实现。HTTP 客户端在 kacore/providers/。
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
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_REASONING_ONLY,
    STATUS_TIMEOUT,
    GenerationResult,
)

logger = logging.getLogger("LLMAdapter")


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
        """是否有可用的对话模型（未配置时所有生成都是离线占位）。

        client 可声明 `configured`（如 ModelRouter 的 RoleClient 在「模型分配」为空时为 False）。
        """
        return self._client is not None and bool(getattr(self._client, "configured", True))

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
                    "No real LLM response (no model, timeout, error or empty response; see the "
                    "previous LLMAdapter ERROR/WARNING in the log); mock fallback is disabled"
                )
            # 降级到离线占位答案是「回答看起来正常、内容其实是假的」，必须是 WARNING 级。
            logger.warning(
                "The main LLM returned nothing, so this answer uses offline placeholder text (not "
                "a real answer); assign a chat model under “Model assignment” and check the errors "
                "above."
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
        if not self.configured:
            logger.warning(
                "LLMAdapter has no chat model assigned, so the main LLM cannot be called."
            )
            return GenerationResult(text="", status=STATUS_EMPTY)

        started = time.monotonic()
        try:
            result = await self._await_client(prompt, system_prompt)
            if not result.text and result.reasoning_present:
                retry_system = (system_prompt or "") + self._REASONING_RETRY_SUFFIX
                result = await self._await_client(prompt, retry_system)
            if result.text:
                logger.info(
                    "main LLM call done in %.1fs prompt=%d chars response=%d chars",
                    time.monotonic() - started,
                    len(prompt),
                    len(result.text),
                )
                return result
        except TimeoutError:
            logger.error(
                "Main LLM call timed out (%ss, prompt %d chars): stopped waiting for this answer. "
                "Check that the model endpoint is reachable and its concurrency limits, or raise "
                "ask.llm_timeout_seconds.",
                self._timeout_seconds,
                len(prompt),
            )
            return GenerationResult(text="", status=STATUS_TIMEOUT)
        except Exception as e:
            logger.error(
                "Main LLM call failed (%.1fs): %s: %s",
                time.monotonic() - started,
                type(e).__name__,
                e,
                exc_info=True,
            )
            return GenerationResult(text="", status=STATUS_ERROR)

        if result.status == STATUS_REASONING_ONLY or result.reasoning_present:
            logger.warning(
                "The main LLM returned only reasoning_content twice without a final answer (%.1fs, "
                "prompt %d chars, finish_reason=%s).",
                time.monotonic() - started,
                len(prompt),
                result.finish_reason or "unknown",
            )
            return replace(result, status=STATUS_REASONING_ONLY)
        logger.warning(
            "The main LLM returned an empty response (%.1fs, prompt %d chars, status=%s): the "
            "model may be rate limited or the output filtered.",
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
            f"Based on the knowledge base, here is the answer to “{question[:60]}”:\n\nThis is "
            "an offline placeholder answer. Assign a chat model under “Model assignment” in "
            "the data flow to get real answers."
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
