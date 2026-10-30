"""识图 API 子门面：估算、启动 / 取消「补全图表描述」、进度、测试识图模型。

为什么单列：api.py 已远超 600 行。这里只编排：哪些图要做、并发多少、什么错误要整体停下；
单张图怎么渲染、怎么问模型在 `pipelines/figure_caption_pipeline.py`。

触发方式（2.1.0 已定）：手动，按集合（或全部）只补缺失（pending / failed）。
每篇文档做完就重建它的图块并通知自动重建索引，中途取消也不丢已完成的描述。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from kacore.domain.figures import (
    FIGURE_DONE,
    FIGURE_FAILED,
    FIGURE_MISSING_STATUSES,
    DocumentFigure,
)
from kacore.domain.models import DocumentLifecycle
from kacore.managers.ingest_manager import META_SCANNED_PAGES
from kacore.providers.base import (
    ERROR_AUTH,
    ERROR_NO_IMAGE_INPUT,
    ERROR_NO_KEY,
    ERROR_NOT_FOUND,
    ROLE_VISION,
    ProviderError,
)
from kacore.vision_job import (
    VISION_CANCELLED,
    VISION_ERROR,
    VISION_PARTIAL,
    VISION_RUNNING,
    VISION_SUCCESS,
    VisionBuildJob,
)

if TYPE_CHECKING:
    from kacore.config import Config
    from kacore.domain.models import SourceDocument
    from kacore.managers.base import BaseIngestManager
    from kacore.pipelines.figure_caption_pipeline import FigureCaptionPipeline
    from kacore.providers.router import ModelRouter
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("FigureVision")

# 这些错误说明模型或连接根本不能用，再做下一张也一样失败：整个任务停下并提示。
_FATAL_CODES = frozenset({ERROR_NO_IMAGE_INPUT, ERROR_AUTH, ERROR_NO_KEY, ERROR_NOT_FOUND})
# 估算用：一张约 1 百万像素的图输入约 800–1300 token，描述输出约 200–300 token。
_EST_PROMPT_TOKENS = 1300
_EST_COMPLETION_TOKENS = 300


class VisionUnavailableError(RuntimeError):
    """识图没开、没选模型或没有原 PDF：给界面的可读原因（HTTP 层转成 409）。"""


class VisionApiMixin:
    """识图任务编排。依赖由 KnowledgeRepositoryApi 构造器注入。"""

    _source_store: SourceDocumentStore
    _ingest_manager: BaseIngestManager | None
    _config: Config | None
    _model_router: ModelRouter | None
    _figure_caption_pipeline: FigureCaptionPipeline | None
    _vision_job: VisionBuildJob | None
    _vision_task: asyncio.Task[None] | None

    # ── 状态与估算 ─────────────────────────────────────────────

    def _vision_settings(self) -> tuple[bool, str, int, int]:
        """(开关, 模型标签, 并发数, 单张超时)。"""
        cfg = self._config.get_vision_config() if self._config is not None else None
        model = self._model_router.describe(ROLE_VISION) if self._model_router else ""
        if cfg is None:
            return False, model, 1, 120
        return cfg.enabled, model, cfg.max_concurrency, cfg.timeout_sec

    async def _vision_docs(self, collection: str | None) -> list[SourceDocument]:
        if collection:
            # 与图谱构建一致：集合连同全部子集合；查不到集合对象时按文档的主归属名筛。
            col = await self._source_store.get_collection_by_name(collection)
            if col is not None:
                docs = await self._source_store.list_documents_by_collection_key(
                    col.coll_key, descendants=True
                )
            else:
                docs = await self._source_store.list_documents(collection=collection)
        else:
            docs = await self._source_store.list_documents()
        return [d for d in docs if d.lifecycle_state == DocumentLifecycle.ACTIVE]

    async def get_vision_status(self, collection: str | None = None) -> dict[str, Any]:
        """集合（或全部）的图表空位统计 + 识图开关与模型，供界面估算与按钮状态。"""
        enabled, model, concurrency, _ = self._vision_settings()
        docs = await self._vision_docs(collection)
        doc_ids = [d.doc_id for d in docs]
        counts = await self._source_store.count_figures_by_status(doc_ids)
        missing = sum(counts.get(s, 0) for s in FIGURE_MISSING_STATUSES)
        scanned_docs = sum(1 for d in docs if d.local_meta.get(META_SCANNED_PAGES))
        return {
            "collection": collection or "",
            "enabled": enabled,
            "model": model,
            "configured": bool(model),
            "max_concurrency": concurrency,
            "documents": len(docs),
            "figures": sum(counts.values()),
            "counts": counts,
            "missing": missing,
            "scanned_documents": scanned_docs,
            "estimated_prompt_tokens": missing * _EST_PROMPT_TOKENS,
            "estimated_completion_tokens": missing * _EST_COMPLETION_TOKENS,
            "active_job": self.get_active_vision_job(),
        }

    def get_active_vision_job(self) -> dict[str, Any] | None:
        """进行中或最近一次任务的快照；从未运行过时为 None。"""
        job = getattr(self, "_vision_job", None)
        return job.to_dict() if job is not None else None

    # ── 启动 / 取消 ─────────────────────────────────────────────

    async def start_vision_build(self, collection: str | None = None) -> dict[str, Any]:
        """后台补全集合（或全部）里缺描述的图。已有任务在跑时直接返回它（全局单任务）。"""
        current = getattr(self, "_vision_job", None)
        if current is not None and current.status == VISION_RUNNING:
            return current.to_dict()
        enabled, model, concurrency, timeout = self._vision_settings()
        if not enabled:
            raise VisionUnavailableError(
                "Figure recognition is off; turn it on in Workflow › Upload / Chunk"
            )
        if not model or self._model_router is None:
            raise VisionUnavailableError(
                "No vision model is assigned; pick one in Workflow › Model assignment"
            )
        if self._ingest_manager is None or self._figure_caption_pipeline is None:
            raise VisionUnavailableError("Figure recognition is not available in this build")
        docs = await self._vision_docs(collection)
        figures = await self._source_store.list_figures(
            statuses=set(FIGURE_MISSING_STATUSES), doc_ids=[d.doc_id for d in docs]
        )
        job = VisionBuildJob(collection=collection or "", model=model)
        by_doc: dict[str, list[DocumentFigure]] = {}
        for fig in figures:
            by_doc.setdefault(fig.doc_id, []).append(fig)
        job.total_docs = len(by_doc)
        job.total_figures = len(figures)
        self._vision_job = job
        logger.info(
            "Figure recognition started: collection=%r documents=%d figures=%d model=%s "
            "concurrency=%d", collection or "*", job.total_docs, job.total_figures, model,
            concurrency,
        )
        docs_by_id = {d.doc_id: d for d in docs}
        self._vision_task = asyncio.create_task(
            self._run_vision_build(job, by_doc, docs_by_id, concurrency, timeout)
        )
        return job.to_dict()

    async def cancel_vision_build(self) -> dict[str, Any] | None:
        """请求取消：已发出的请求做完、写回，不再发新的。"""
        job = getattr(self, "_vision_job", None)
        if job is not None and job.status == VISION_RUNNING:
            job.cancel_requested = True
            logger.info("Figure recognition cancel requested job=%s", job.job_id)
        return self.get_active_vision_job()

    async def shutdown_vision(self) -> None:
        task = getattr(self, "_vision_task", None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._vision_task = None

    # ── 后台任务 ───────────────────────────────────────────────

    async def _run_vision_build(
        self,
        job: VisionBuildJob,
        by_doc: dict[str, list[DocumentFigure]],
        docs_by_id: dict[str, SourceDocument],
        concurrency: int,
        timeout: int,
    ) -> None:
        assert self._model_router is not None and self._figure_caption_pipeline is not None
        client = self._model_router.client_for(ROLE_VISION, timeout_sec=timeout, max_retries=1)
        semaphore = asyncio.Semaphore(concurrency)
        started = time.monotonic()
        try:
            for doc_id, figures in by_doc.items():
                if job.cancel_requested or job.status == VISION_ERROR:
                    break
                doc = docs_by_id[doc_id]
                job.current_doc = doc.title
                await self._describe_document(job, doc, figures, client, semaphore)
                job.processed_docs += 1
            if job.status != VISION_ERROR:
                job.finish(
                    VISION_CANCELLED if job.cancel_requested
                    else VISION_PARTIAL if job.failed else VISION_SUCCESS
                )
        except asyncio.CancelledError:
            job.finish(VISION_CANCELLED)
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务统一写终态，不向外抛
            logger.error("Figure recognition crashed: %s", exc, exc_info=True)
            job.recent_error = str(exc)
            job.finish(VISION_ERROR)
        finally:
            logger.info(
                "Figure recognition %s: described=%d failed=%d skipped=%d of %d, documents %d/%d, "
                "tokens in/out %d/%d, %.0fs", job.status, job.described, job.failed, job.skipped,
                job.total_figures, job.processed_docs, job.total_docs, job.prompt_tokens,
                job.completion_tokens, time.monotonic() - started,
            )

    async def _describe_document(
        self,
        job: VisionBuildJob,
        doc: SourceDocument,
        figures: list[DocumentFigure],
        client: Any,
        semaphore: asyncio.Semaphore,
    ) -> None:
        assert self._ingest_manager is not None and self._figure_caption_pipeline is not None
        pipeline = self._figure_caption_pipeline
        source_pdf = getattr(self._ingest_manager, "source_pdf_path", lambda _d: None)(doc)
        if source_pdf is None:
            logger.warning("Skipping %s: original PDF not found", doc.doc_id)
            job.skipped += len(figures)
            job.recent_error = f"Original PDF not found for “{doc.title}”"
            return
        changed = False

        async def one(fig: DocumentFigure) -> None:
            nonlocal changed
            async with semaphore:
                if job.cancel_requested or job.status == VISION_ERROR:
                    return
                job.current_figure = f"{fig.label or 'Figure'} · p.{fig.page}"
                try:
                    updated, result = await pipeline.describe(
                        fig, str(source_pdf), client, model_label=job.model
                    )
                except ProviderError as error:
                    self._record_vision_failure(job, fig, error)
                    if error.code not in _FATAL_CODES:
                        updated = self._failed_copy(fig, error.message, job.model)
                        await self._source_store.update_figure_result(updated)
                    return
                if result is not None:
                    job.prompt_tokens += result.prompt_tokens or 0
                    job.completion_tokens += result.completion_tokens or 0
                await self._source_store.update_figure_result(updated)
                if updated.status == FIGURE_DONE:
                    job.described += 1
                    changed = True
                else:
                    job.failed += 1
                    job.recent_error = updated.error

        await asyncio.gather(*(one(fig) for fig in figures))
        if changed:
            await self._ingest_manager.refresh_figure_chunks(doc.doc_id)  # type: ignore[attr-defined]
            self._notify_auto_reindex()  # type: ignore[attr-defined]  # KnowledgeRepositoryApi 提供

    def _record_vision_failure(
        self, job: VisionBuildJob, fig: DocumentFigure, error: ProviderError
    ) -> None:
        job.recent_error = error.message
        if error.code in _FATAL_CODES:
            if job.status != VISION_ERROR:
                logger.error(
                    "Figure recognition stopped (%s): %s", error.code, error.message
                )
            job.finish(VISION_ERROR)
            return
        job.failed += 1
        logger.warning(
            "Figure %s failed (%s): %s", fig.figure_id, error.code, error.message
        )

    @staticmethod
    def _failed_copy(fig: DocumentFigure, message: str, model: str) -> DocumentFigure:
        from dataclasses import replace
        from datetime import datetime, timezone

        return replace(
            fig, status=FIGURE_FAILED, error=message[:500], model=model,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── 测试识图模型 ───────────────────────────────────────────

    async def test_vision_model(self) -> dict[str, Any]:
        """发一张本地画的小图（一根红色柱子），问颜色：验证所选模型真的能看图。不抛错。"""
        _, model, _, timeout = self._vision_settings()
        if not model or self._model_router is None:
            return {"status": "error", "code": ERROR_NOT_FOUND,
                    "message": "No vision model is assigned yet"}
        started = time.monotonic()
        client = self._model_router.client_for(ROLE_VISION, timeout_sec=timeout, max_retries=0)
        try:
            png = await asyncio.to_thread(_probe_png)
            result = await client.describe_image(
                "What color is the single bar in this image? Answer with one word.",
                png, max_tokens=20,
            )
        except ProviderError as error:
            return {"status": "error", "code": error.code, "message": error.message,
                    "model": model}
        answer = result.text.strip()
        elapsed = round((time.monotonic() - started) * 1000)
        if "red" not in answer.lower() and "红" not in answer:
            return {"status": "warning", "model": model, "answer": answer[:200],
                    "elapsed_ms": elapsed,
                    "message": "The model answered but did not see the red bar; it may not "
                               "read images reliably"}
        logger.info("Vision model test ok model=%s answer=%r %dms", model, answer[:40], elapsed)
        return {"status": "ok", "model": model, "answer": answer[:200], "elapsed_ms": elapsed}

    async def list_document_figures_public(self, doc_id: str) -> list[dict[str, Any]]:
        """文档的图空位（界面「图表」列表用）；不含截图。"""
        return [
            {
                "figure_id": f.figure_id, "page": f.page, "label": f.label,
                "caption": f.caption, "status": f.status, "description": f.description,
                "model": f.model, "error": f.error, "bbox": list(f.bbox),
                "has_inner_text": bool(f.inner_text.strip()),
            }
            for f in await self._source_store.list_document_figures(doc_id)
        ]


def _probe_png() -> bytes:
    """测试图：白底上一根红色柱子 + 坐标轴（PyMuPDF 现画，不依赖任何图片文件）。"""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_line((30, 20), (30, 130))
    page.draw_line((30, 130), (190, 130))
    page.draw_rect(pymupdf.Rect(80, 40, 130, 130), color=(0.8, 0, 0), fill=(0.9, 0.1, 0.1))
    png = page.get_pixmap(dpi=96, alpha=False).tobytes("png")
    doc.close()
    return bytes(png)


__all__ = ["VisionApiMixin", "VisionUnavailableError"]
