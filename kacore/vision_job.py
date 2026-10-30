"""识图任务的进度模型（domain-light，无 I/O）。

为什么存在：「补全图表描述」是一次可能几十分钟、按图计费的后台任务。本模块定义一个纯内存的进度
快照，由 `kacore/api_vision.py` 的后台任务逐图更新，经 HTTP 给左下角进度栏轮询。

与 LightRAG 构建一样手动触发；与 Milvus 重建一样全局单任务。可取消：已发出的请求做完，不再发新的；
再点一次「补全」只处理还缺描述（pending / failed）的图。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

VISION_RUNNING = "running"
VISION_SUCCESS = "success"
VISION_PARTIAL = "partial_failure"  # 有图失败（failed_figures > 0）
VISION_CANCELLED = "cancelled"
VISION_ERROR = "error"  # 整体失败（如模型不收图片、密钥无效），一张都没法做

VISION_TERMINAL_STATUSES = frozenset(
    {VISION_SUCCESS, VISION_PARTIAL, VISION_CANCELLED, VISION_ERROR}
)


@dataclass
class VisionBuildJob:
    """单次「补全图表描述」任务的快照。

    契约：
    - `total_figures` 是本次要处理的图（开始时的 pending + failed）；`described + failed + skipped
      <= total_figures`，三者之和推动 `progress_percent`。
    - `current_doc` / `current_figure` 只供界面显示正在处理哪张图。
    - `recent_error` 保留最近一条失败原因（已脱敏，来自 ProviderError.message）。
    """

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    collection: str = ""
    model: str = ""
    status: str = VISION_RUNNING
    total_docs: int = 0
    processed_docs: int = 0
    total_figures: int = 0
    described: int = 0
    failed: int = 0
    skipped: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    current_doc: str = ""
    current_figure: str = ""
    recent_error: str = ""
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def done_figures(self) -> int:
        return self.described + self.failed + self.skipped

    @property
    def progress_percent(self) -> int:
        if self.total_figures <= 0:
            return 100 if self.status in VISION_TERMINAL_STATUSES else 0
        return min(100, round(self.done_figures * 100 / self.total_figures))

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = time.time()
        self.current_doc = ""
        self.current_figure = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "collection": self.collection,
            "model": self.model,
            "status": self.status,
            "total_docs": self.total_docs,
            "processed_docs": self.processed_docs,
            "total_figures": self.total_figures,
            "described": self.described,
            "failed": self.failed,
            "skipped": self.skipped,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "progress_percent": self.progress_percent,
            "current_doc": self.current_doc,
            "current_figure": self.current_figure,
            "recent_error": self.recent_error,
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


__all__ = [
    "VISION_CANCELLED",
    "VISION_ERROR",
    "VISION_PARTIAL",
    "VISION_RUNNING",
    "VISION_SUCCESS",
    "VISION_TERMINAL_STATUSES",
    "VisionBuildJob",
]
