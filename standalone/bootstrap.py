"""独立壳装配入口：构造壳差异注入件并交给组合根（不自建第二个组合根）。

注入规则（ARCHITECTURE.md §2）：
- 主 LLM：``main_llm`` 配置完整（base_url+model）时注入 ``LMStudioLLMAdapter``
  （任意 OpenAI-compatible endpoint）；未配置则回退 ``LLMAdapter(None)`` 的离线降级行为。
- KB 回落读取：注入 ``InMemoryKnowledgeBaseReader``（无 AstrBot 内置 KB 可读）。
- Notion 传输：注入 ``StdioNotionToolCaller``（惰性建会话，未启用 Notion 则永不 spawn；
  会话由组合根 teardown 统一释放）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kacore.config import Config
from kacore.plugin_initializer import PluginInitializer
from kacore.repository.kb_reader.memory import InMemoryKnowledgeBaseReader
from kacore.runtime_config import RuntimeConfigStore
from standalone.notion_transport import StdioNotionToolCaller

if TYPE_CHECKING:
    from pathlib import Path


def resolve_main_llm_adapter(data_dir: Path, raw_config: dict[str, Any]) -> Any | None:
    """按与组合根一致的合并链（runtime_config.json 覆盖 raw）解析主 LLM 适配器。"""
    merged = RuntimeConfigStore(data_dir / "runtime_config.json").merged_with(raw_config)
    main_llm = Config(merged).get_main_llm_config()
    if not main_llm.configured:
        return None
    from kacore.adapters.llm import LMStudioLLMAdapter

    return LMStudioLLMAdapter(
        base_url=main_llm.base_url,
        model=main_llm.model,
        api_key=main_llm.api_key,
        timeout_seconds=main_llm.timeout_seconds,
        max_retries=main_llm.max_retries,
    )


def build_initializer(
    data_dir: Path,
    raw_config: dict[str, Any],
    *,
    notion_tool_caller: Any | None = None,
) -> PluginInitializer:
    """构造独立壳组合根（未 initialize；生命周期由组合根托管）。"""
    if notion_tool_caller is None:
        notion_tool_caller = StdioNotionToolCaller()
    return PluginInitializer(
        None,
        raw_config,
        data_dir,
        llm_adapter=resolve_main_llm_adapter(data_dir, raw_config),
        kb_reader=InMemoryKnowledgeBaseReader(),
        notion_tool_caller=notion_tool_caller,
        shell="standalone",
    )


__all__ = ["build_initializer", "resolve_main_llm_adapter"]
