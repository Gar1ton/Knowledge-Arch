"""「模型思考」开关：一次提问内关闭模型自带的推理（Claude thinking、Qwen3 思考模式等）。

开关按请求生效，不进设置：`ask` 在自己的任务里设 `model_thinking`，同一任务（含它派生的子任务）
里的每次模型调用都看得到；建图、清洗等后台任务不受影响。各家关闭参数不同，按连接换算：

- Anthropic：按型号。Opus 5.5 / Opus 5 / Sonnet 5.5 / Fable / Mythos 关不掉思考（或关闭与服务端
  拒答回退冲突），改用最低 effort；Haiku 5.5 / Sonnet 5 / Opus 4.8 / 4.7 发 `thinking: disabled`；
  更早的型号缺省就不思考，不发参数。
- OpenAI 兼容：按地址识别服务商；认不出的（vLLM / SGLang / LM Studio / 自定义）发 Qwen3 一类
  模板通用的 `chat_template_kwargs.enable_thinking=false`。

参数被端点拒收（4xx）时由调用方去掉参数重试一次，开关只会「不起作用」，不会让提问失败。
本模块只给参数，不发请求。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

# True = 保持模型默认（开关打开）；False = 本次提问尽量关闭模型思考。
model_thinking: ContextVar[bool] = ContextVar("model_thinking", default=True)

_LOW_EFFORT: dict[str, Any] = {"output_config": {"effort": "low"}}
_DISABLED: dict[str, Any] = {"thinking": {"type": "disabled"}}

# 先长后短：claude-opus-5-5 不能被 claude-opus-5 抢先匹配。
_ANTHROPIC_RULES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("claude-fable", _LOW_EFFORT),
    ("claude-mythos", _LOW_EFFORT),
    ("claude-opus-5-5", _LOW_EFFORT),
    ("claude-sonnet-5-5", _LOW_EFFORT),
    ("claude-haiku-5-5", _DISABLED),
    ("claude-opus-5", _LOW_EFFORT),
    ("claude-sonnet-5", _DISABLED),
    ("claude-opus-4-8", _DISABLED),
    ("claude-opus-4-7", _DISABLED),
)

# (地址里的域名片段, 关闭参数)；按顺序第一个命中生效。
_OPENAI_COMPAT_RULES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("dashscope", {"enable_thinking": False}),
    ("siliconflow", {"enable_thinking": False}),
    ("bigmodel.cn", {"thinking": {"type": "disabled"}}),
    ("z.ai", {"thinking": {"type": "disabled"}}),
    ("deepseek.com", {"thinking": {"type": "disabled"}}),
    ("moonshot", {"thinking": {"type": "disabled"}}),
    ("openrouter.ai", {"reasoning": {"effort": "none"}}),
    ("api.openai.com", {"reasoning_effort": "none"}),
)
_OLLAMA_PORT = 11434
_OLLAMA_OFF: dict[str, Any] = {"reasoning_effort": "none"}
_TEMPLATE_OFF: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}


def thinking_disabled() -> bool:
    """当前调用是否处在「关闭模型思考」的提问里。"""
    return not model_thinking.get()


def anthropic_thinking_off(model: str) -> dict[str, Any]:
    """关闭（或压到最低）思考时追加到 messages.create 的参数；不需要时返回空字典。"""
    for prefix, params in _ANTHROPIC_RULES:
        if model == prefix or model.startswith(prefix + "-"):
            return {k: dict(v) for k, v in params.items()}
    return {}


def openai_compat_thinking_off(base_url: str) -> dict[str, Any]:
    """关闭思考时追加到 chat/completions 请求体的字段（按连接地址识别服务商）。"""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    for fragment, params in _OPENAI_COMPAT_RULES:
        if fragment in host:
            return {k: v if not isinstance(v, dict) else dict(v) for k, v in params.items()}
    if parsed.port == _OLLAMA_PORT or "ollama" in host:
        return dict(_OLLAMA_OFF)
    return {"chat_template_kwargs": dict(_TEMPLATE_OFF["chat_template_kwargs"])}


__all__ = [
    "anthropic_thinking_off",
    "model_thinking",
    "openai_compat_thinking_off",
    "thinking_disabled",
]
