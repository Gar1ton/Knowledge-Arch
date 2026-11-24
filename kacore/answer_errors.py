"""问答链路的异常（core 横切支撑；P34 从 api.py 原样移出）。

web 层与测试仍可从 `kacore.api` 导入这些名字（api.py 再导出），这里是定义处。
"""
from __future__ import annotations

from kacore.retrieval_modes import MODE_GRAPH_MIXED


class LightRAGNotReadyError(RuntimeError):
    def __init__(self, collection: str, reason: str, *, build_available: bool = False) -> None:
        super().__init__(reason)
        self.collection = collection
        self.reason = reason
        self.build_available = build_available


class GraphMixedQueryError(RuntimeError):
    """图谱混合检索已就绪但查询执行失败。"""

    def __init__(
        self, collection: str, reason: str, *, mode: str = MODE_GRAPH_MIXED
    ) -> None:
        super().__init__(reason)
        self.collection = collection
        self.reason = reason
        self.mode = mode


class AskTaskTimeoutError(RuntimeError):
    """整条 ask() 任务超过 `ask.task_timeout_seconds` 仍未完成。

    非致命——底层任务并未被取消，仍在后台继续运行并会在完成后正常写入 chat_history；
    调用方（web 层）据此返回一个可区分的状态码，指引前端改为轮询
    `/api/ask/progress/{cid}` 与 `/api/chat/history` 找回迟到的答案。
    """

    def __init__(self, conversation_id: str, timeout_seconds: int) -> None:
        super().__init__(
            f"ask task exceeded task_timeout_seconds={timeout_seconds}s "
            f"(conversation_id={conversation_id}); still running in background"
        )
        self.conversation_id = conversation_id
        self.timeout_seconds = timeout_seconds


class AskCancelledError(RuntimeError):
    """ask() 任务在完成前被 `cancel_ask()` 中断（清空聊天时）。

    任务已取消、不会再写 chat_history；调用方（web 层）据此告诉前端这次提问已放弃，
    不是失败，也不必再补轮询。
    """

    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"ask task was cancelled (conversation_id={conversation_id})")
        self.conversation_id = conversation_id


class FullTextConfirmationRequiredError(RuntimeError):
    """全文读取量超过确认阈值，且调用方尚未显式确认。

    **不是失败，是一次询问**：调用方（web 层 → 前端弹窗 / codex skill → preview）应把
    `total_chars` 等数字原样展示给用户，取得明确同意后带 `confirmed=True` 重发同一请求。
    抛出时保证**尚未调用 LLM、尚未写入任何 chat_history**，重发是干净的重试而非续跑。

    刻意携带全部展示所需数字：调用方据此直接渲染，无需再打一次 estimate 请求。
    """

    def __init__(
        self,
        doc_id: str,
        *,
        title: str,
        total_chars: int,
        requested_chars: int,
        threshold_chars: int,
        estimated_tokens: int,
    ) -> None:
        super().__init__(
            f"full-text read of {requested_chars} chars exceeds the "
            f"{threshold_chars}-char confirmation threshold; resend with confirmed=true"
        )
        self.doc_id = doc_id
        self.title = title
        self.total_chars = total_chars
        self.requested_chars = requested_chars
        self.threshold_chars = threshold_chars
        self.estimated_tokens = estimated_tokens


class FullTextDocumentUnavailableError(RuntimeError):
    """全文检索指定的文档不可读：库里没有，或 clean.md 制品缺失。

    分两种 reason 是因为用户能做的补救不同：`document_not_found` 是选错了文档，
    `artifact_missing` 是文档在库但解析产物丢了（需要重新入库）。
    """

    def __init__(self, doc_id: str, reason: str) -> None:
        super().__init__(f"full-text document unavailable ({reason}): {doc_id}")
        self.doc_id = doc_id
        self.reason = reason


class FullTextGenerationFailedError(RuntimeError):
    """整篇正文已成功读出，但 LLM 生成失败（最常见原因是超出上下文窗口）。

    单列一类而不是复用通用错误：这条路径的失败几乎总是「文档太长」，用户需要的不是
    「生成失败」四个字，而是**实际字符数**加一句可执行的建议（改用 default/enhanced）。
    契约：绝不返回截断后的半篇答案冒充完整回答。
    """

    def __init__(self, doc_id: str, total_chars: int, reason: str) -> None:
        super().__init__(reason)
        self.doc_id = doc_id
        self.total_chars = total_chars
        self.reason = reason


__all__ = [
    "AskCancelledError",
    "AskTaskTimeoutError",
    "FullTextConfirmationRequiredError",
    "FullTextDocumentUnavailableError",
    "FullTextGenerationFailedError",
    "GraphMixedQueryError",
    "LightRAGNotReadyError",
]
