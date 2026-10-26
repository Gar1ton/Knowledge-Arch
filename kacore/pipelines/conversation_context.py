"""多轮对话上下文（pipelines 层）：把追问改写成可独立检索的完整问题。

只取本会话最近几轮（问题原文 + 截断后的答案），由 api.ask 调 LLM 改写；改写结果同时用于
检索与生成（六种检索模式都只认一个问题），聊天记录仍保存用户原话。本模块全是纯函数。
"""

from __future__ import annotations

MAX_TURNS = 3
MAX_ANSWER_CHARS = 600
MAX_QUESTION_CHARS = 500

_CONDENSE_SYSTEM = (
    "You rewrite follow-up questions for a research assistant that searches a document library. "
    "Using the conversation, rewrite the follow-up into one standalone question that can be "
    "understood without the conversation: resolve pronouns and references such as 'it', "
    "'that method' or 'the second paper' to what they refer to. Keep the language of the "
    "follow-up question. If it is already standalone, return it unchanged. Do not answer it. "
    "Output only the rewritten question."
)


def recent_turns(messages: list[dict], max_turns: int = MAX_TURNS) -> list[tuple[str, str]]:
    """把 chat_history（按 id 升序）配成（问题, 答案）对，取最后 max_turns 轮。

    没有答案的问题（如超时后迟到前的提问）跳过。
    """
    turns: list[tuple[str, str]] = []
    pending: str | None = None
    for message in messages:
        content = str(message.get("content") or "").strip()
        if message.get("role") == "user":
            pending = content
        elif message.get("role") == "assistant" and pending:
            turns.append((pending, content))
            pending = None
    return turns[-max_turns:] if max_turns > 0 else []


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def build_condense_prompt(turns: list[tuple[str, str]], question: str) -> tuple[str, str]:
    """返回（user prompt, system prompt）。答案只留开头一段：指代消解用不到参考文献与细节。"""
    history = "\n\n".join(
        f"User: {asked}\nAssistant: {_clip(answer, MAX_ANSWER_CHARS)}" for asked, answer in turns
    )
    user = f"Conversation so far:\n\n{history}\n\nFollow-up question: {question}"
    return user, _CONDENSE_SYSTEM


def clean_rewrite(text: str) -> str:
    """取模型输出的第一行非空文本，去掉包裹引号与「Rewritten question:」之类前缀；过长视为无效。"""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    for prefix in ("Standalone question:", "Rewritten question:", "Question:"):
        if line.lower().startswith(prefix.lower()):
            line = line[len(prefix):].strip()
    line = line.strip("\"'“”‘’「」 ")
    return line if len(line) <= MAX_QUESTION_CHARS else ""


__all__ = ["build_condense_prompt", "clean_rewrite", "recent_turns"]
