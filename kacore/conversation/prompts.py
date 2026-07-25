"""统一对话 Prompt 与工具 schema；仅描述协议，不执行任何业务。"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = (
    "你是 Knowledge Arch 的研究对话编排助手。Research 开启时，先调用 "
    "research_scope_probe 探查范围；低歧义可直接调用 research_execute，中高歧义必须先用"
    "自然语言向用户确认。工具参数 query 必须是结合上下文改写后的自包含研究问题。"
    "若系统给出固定 collection，任何工具调用都不得越出该范围。Research 的最终答案由工具"
    "直接展示，不要复述。聊天历史只用于理解指代、改写 query 和选择范围，绝不是事实证据；"
    "所有事实必须来自工具返回的知识库引用。"
)

REWRITE_SYSTEM = (
    "Rewrite the current user message into one self-contained knowledge-retrieval question using "
    "the conversation only to resolve references. Do not answer, add facts, or treat conversation "
    "claims as evidence. Output only the rewritten question."
)


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "research_scope_probe",
                "description": "探查知识库中的候选 collection、论文、标签、歧义和建议模式。",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "research_execute",
                "description": "范围明确后执行召回并直接向用户交付结构化答案。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "collection": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "default",
                                "enhanced",
                                "deep_thinking",
                                "graph_mixed",
                                "graph_only",
                            ],
                        },
                        "breadth": {
                            "type": "string",
                            "enum": ["narrow", "normal", "wide"],
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "notion_push_note",
                "description": "把用户明确要求保存的完整结论新增到 Notion 问答库。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "title": {"type": "string"},
                        "tags": {"type": "string"},
                        "citations": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
        },
    ]


__all__ = ["REWRITE_SYSTEM", "SYSTEM_PROMPT", "tool_schemas"]

