"""给一张图写描述：按位置从原 PDF 现渲染 → 识图模型 → 写回空位（pipelines 层）。

为什么存在：描述是检索用的文字，要把图里「看得见」的信息写全（图的类型、坐标轴、图例、关键数值与趋势、
流程图的组件与连接），又不能编造看不清的数。提示词与版本号集中在这里，换提示词就升 PROMPT_VERSION，
已描述的图可据此判断是否需要重做（2.1.0 不自动重做）。

依赖：渲染用 managers/figure_detection（PyMuPDF，线程里跑）；模型经注入的 `VisionClient`
（ModelRouter 的 vision 角色），本模块不直接 import 任何 SDK。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from kacore.domain.figures import FIGURE_DONE, FIGURE_FAILED, DocumentFigure
from kacore.domain.llm_generation import STATUS_LENGTH, STATUS_OK
from kacore.managers.figure_detection import render_figure_image

if TYPE_CHECKING:
    from kacore.domain.llm_generation import GenerationResult

logger = logging.getLogger("FigureVision")

PROMPT_VERSION = "figure-v1"
_MAX_TOKENS = 700
_INNER_TEXT_PROMPT_CHARS = 1200

SYSTEM_PROMPT = (
    "You describe figures from research papers and reports so that they can be found by text "
    "search and cited accurately. Describe only what is visible in the image. Never invent "
    "numbers, labels or trends you cannot read; say a value is unreadable instead."
)


class VisionClient(Protocol):
    async def describe_image(
        self,
        prompt: str,
        image: bytes,
        system_prompt: str = "",
        *,
        max_tokens: int = 800,
        media_type: str = "image/png",
    ) -> GenerationResult: ...


def build_prompt(fig: DocumentFigure) -> str:
    """一张图的用户提示：标题与 PDF 里能抽到的图内文字作为线索，模型以图片为准。"""
    lines = [f"This image is {fig.label or 'a figure'} on page {fig.page} of a document."]
    if fig.caption:
        lines.append(f"Caption: {fig.caption}")
    inner = " · ".join(t.strip() for t in fig.inner_text.splitlines() if t.strip())
    if inner:
        clipped = inner[:_INNER_TEXT_PROMPT_CHARS]
        lines.append(f"Text found inside the figure (may be incomplete): {clipped}")
    lines.append(
        "Write a description for search, 80-200 words of plain prose, no headings or lists:\n"
        "1. What kind of figure it is (line chart, bar chart, scatter plot, table, diagram, "
        "architecture, photo, map, ...).\n"
        "2. For charts: axis labels and units, value ranges, each series or legend entry, the key "
        "values you can read, trends, comparisons, maxima and minima.\n"
        "3. For diagrams: the components and how they connect or flow.\n"
        "4. The main point the figure makes.\n"
        "Use the same language as the caption (English if there is no caption)."
    )
    return "\n".join(lines)


class FigureCaptionPipeline:
    """单张图的识图流程；并发、取消与进度由调用方（api_vision 的后台任务）负责。"""

    def __init__(self, *, render_dpi: int = 150) -> None:
        self._render_dpi = render_dpi

    async def describe(
        self, fig: DocumentFigure, pdf_path: str, client: VisionClient, *, model_label: str
    ) -> tuple[DocumentFigure, GenerationResult | None]:
        """返回写好结果的空位副本（done 或 failed）与模型返回；模型调用异常向上抛（由调用方分类）。

        渲染失败（如 PDF 已被替换、位置越界）记为 failed 而不调用模型。
        """
        started = time.monotonic()
        now = datetime.now(timezone.utc).isoformat()
        try:
            image, media_type = await asyncio.to_thread(
                render_figure_image, pdf_path, fig.page, fig.bbox, dpi=self._render_dpi
            )
        except Exception as exc:
            logger.warning("Render failed %s: %s", fig.figure_id, exc)
            return replace(fig, status=FIGURE_FAILED, error=f"render failed: {exc}",
                           updated_at=now), None
        image_hash = hashlib.sha256(image).hexdigest()
        result = await client.describe_image(
            build_prompt(fig), image, SYSTEM_PROMPT, max_tokens=_MAX_TOKENS, media_type=media_type
        )
        text = result.text.strip()
        usable = result.status in {STATUS_OK, STATUS_LENGTH} and bool(text)
        logger.debug(
            "Figure %s (%s p.%d) %dx%d %s bytes=%d -> status=%s chars=%d tokens=%s/%s %.1fs "
            "model=%s",
            fig.figure_id, fig.label or "-", fig.page, round(fig.bbox[2] - fig.bbox[0]),
            round(fig.bbox[3] - fig.bbox[1]), media_type, len(image), result.status, len(text),
            result.prompt_tokens, result.completion_tokens, time.monotonic() - started,
            model_label,
        )
        if not usable:
            return replace(
                fig, status=FIGURE_FAILED, image_hash=image_hash, model=model_label,
                prompt_version=PROMPT_VERSION, error=f"empty answer ({result.status})",
                updated_at=now,
            ), result
        return replace(
            fig, status=FIGURE_DONE, description=text, image_hash=image_hash, model=model_label,
            prompt_version=PROMPT_VERSION, error="", updated_at=now,
        ), result


__all__ = [
    "FigureCaptionPipeline", "PROMPT_VERSION", "SYSTEM_PROMPT", "VisionClient", "build_prompt",
]
