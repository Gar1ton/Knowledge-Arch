"""提示词语言（pipelines 层）：按回答语言选中文或英文提示词。

answer_language 为 zh / en 时直接用；auto 跟随问题语言（含中文即中文，否则英文）。
两种语言的提示词只有说明文字与示例值不同，JSON 字段名、枚举值与解析契约完全一致
（tests/backend/test_prompt_language.py 核对）。
"""

from __future__ import annotations

from typing import Literal

PromptLang = Literal["zh", "en"]


def uses_chinese(text: str) -> bool:
    """粗略判断文本是否包含中文。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def prompt_lang(answer_language: str, question: str) -> PromptLang:
    if answer_language == "zh":
        return "zh"
    if answer_language == "en":
        return "en"
    return "zh" if uses_chinese(question) else "en"


__all__ = ["PromptLang", "prompt_lang", "uses_chinese"]
