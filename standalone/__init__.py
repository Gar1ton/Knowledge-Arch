"""独立应用薄壳（standalone shell，见 ARCHITECTURE.md §2 一核两壳）。

只做注册与委派：CLI 子命令解析、数据目录解析、standalone_config.json 引导，
然后经组合根 ``PluginInitializer`` 注入壳差异（OpenAI-compatible 主 LLM、内存 KB
读取器、stdio Notion 传输）并托管生命周期。业务逻辑一律在 ``kacore``。

``__version__`` 与根 ``pyproject.toml`` ``[project].version`` 对齐，由
``tools/bump_version.py --target standalone`` 统一推进。
"""
from __future__ import annotations

__version__ = "v0.2.2"

__all__ = ["__version__"]
