"""「+ 添加」时可选的连接预设（地址与类型）；用户仍可改地址或选「自定义」。"""

from __future__ import annotations

from dataclasses import dataclass

from kacore.providers.anthropic import DEFAULT_BASE_URL as ANTHROPIC_BASE_URL
from kacore.providers.base import KIND_ANTHROPIC, KIND_OPENAI_COMPATIBLE


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    kind: str
    base_url: str
    needs_key: bool = True


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset("openai", "OpenAI", KIND_OPENAI_COMPATIBLE, "https://api.openai.com/v1"),
    ProviderPreset("anthropic", "Anthropic", KIND_ANTHROPIC, ANTHROPIC_BASE_URL),
    ProviderPreset("deepseek", "DeepSeek", KIND_OPENAI_COMPATIBLE, "https://api.deepseek.com/v1"),
    ProviderPreset(
        "dashscope", "DashScope（通义）", KIND_OPENAI_COMPATIBLE,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderPreset("moonshot", "Moonshot（Kimi）", KIND_OPENAI_COMPATIBLE, "https://api.moonshot.cn/v1"),
    ProviderPreset(
        "zhipu", "智谱 GLM", KIND_OPENAI_COMPATIBLE, "https://open.bigmodel.cn/api/paas/v4"
    ),
    ProviderPreset(
        "siliconflow", "SiliconFlow", KIND_OPENAI_COMPATIBLE, "https://api.siliconflow.cn/v1"
    ),
    ProviderPreset("openrouter", "OpenRouter", KIND_OPENAI_COMPATIBLE, "https://openrouter.ai/api/v1"),
    ProviderPreset(
        "ollama", "Ollama", KIND_OPENAI_COMPATIBLE, "http://localhost:11434/v1", needs_key=False
    ),
    ProviderPreset(
        "lmstudio", "LM Studio", KIND_OPENAI_COMPATIBLE, "http://localhost:1234/v1", needs_key=False
    ),
)


def preset_dicts() -> list[dict[str, object]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "kind": p.kind,
            "base_url": p.base_url,
            "needs_key": p.needs_key,
        }
        for p in PRESETS
    ]


__all__ = ["PRESETS", "ProviderPreset", "preset_dicts"]
