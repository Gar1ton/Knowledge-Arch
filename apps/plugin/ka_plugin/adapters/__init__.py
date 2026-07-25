"""AstrBot 壳专属适配器（框架 ↔ kacore domain 的翻译层）。

依赖方向单向：本包可 import ``kacore``，``kacore`` 永不 import 本包。
组合根由 ``apps/plugin/main.py`` 显式注入这些实现。
"""
from ka_plugin.adapters.kb_chunk import to_document_chunk
from ka_plugin.adapters.kb_reader import AstrBotKnowledgeBaseReader
from ka_plugin.adapters.llm import AstrBotLLMAdapter

__all__ = ["AstrBotKnowledgeBaseReader", "AstrBotLLMAdapter", "to_document_chunk"]
