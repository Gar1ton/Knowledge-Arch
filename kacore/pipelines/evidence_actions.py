"""已有自检响应的可选阅读动作；只接受已知动作和当前证据 ID，不执行任意路径。"""

from __future__ import annotations

from typing import Any

from kacore.pipelines.prompt_language import PromptLang

ACTION_GUIDANCE: dict[PromptLang, str] = {
    "en": (
        ' You may also return "actions":[{"kind":"expand_after|expand_before|read_footnote",'
        '"source_id":"an evidence chunk ID","reason":"short evidence gap"}]. '
        "Use actions only when local context is missing; do not invent source IDs."
    ),
    "zh": (
        '还可以返回 "actions":[{"kind":"expand_after|expand_before|read_footnote",'
        '"source_id":"某条证据的 chunk ID","reason":"简短说明证据缺口"}]。'
        "只在缺少局部上下文时使用 actions；不要编造 source ID。"
    ),
}
_READ_KINDS = frozenset({"expand_after", "expand_before", "read_footnote"})


def parse_actions(value: Any) -> list[dict[str, str]]:
    """旧响应没有 actions 时兼容为空；最多八个有界动作，未知字段不执行。"""
    if not isinstance(value, list):
        return []
    return [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "reason": str(item.get("reason", ""))[:200],
        }
        for item in value[:8]
        if isinstance(item, dict)
        and isinstance(item.get("kind"), str)
        and item["kind"] in _READ_KINDS
        and isinstance(item.get("source_id"), str)
    ]
