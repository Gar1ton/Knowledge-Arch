"""旧版插件数据导入的 API 子门面（mixin，组合进 KnowledgeRepositoryApi；欢迎页调用）。

- inspect：预览所选目录里的旧版数据（不写任何东西）；
- start：后台准备 staging（复制 / 改写路径 / 迁移 / 翻译配置），成功后自动软重启换入；
- status：后台进度 + 待应用的导入 + 最近一次结果（软重启后由结果文件接续）；
- discard：放弃尚未换入的导入。
只允许导入到空库（欢迎页场景），避免覆盖已有数据。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.legacy_import.layout import LegacyImportError

if TYPE_CHECKING:
    from kacore.repository.source_store.base import SourceDocumentStore
    from kacore.runtime_events import RuntimeEventRecorder

logger = logging.getLogger("KnowledgeRepositoryApi")


class LegacyImportConflictError(LegacyImportError):
    """当前状态不允许开始导入（库非空 / 已有导入进行中）；HTTP 层转 409。"""


class LegacyImportApiMixin:
    _data_dir: Path | None
    _source_store: SourceDocumentStore
    _runtime_events: RuntimeEventRecorder
    _legacy_import_job: dict[str, Any] | None
    _legacy_import_task: asyncio.Task[None] | None
    # 另依赖 KnowledgeRepositoryApi.restart_service（换入发生在软重启的 initialize 里）。

    def _require_data_dir(self) -> Path:
        if self._data_dir is None:
            raise RuntimeError("This environment cannot import old plugin data.")
        return self._data_dir

    async def _library_is_empty(self) -> bool:
        """含已归档文档：任何已有文档都视为非空库。"""
        return not await self._source_store.list_documents()

    async def inspect_legacy_data(self, path: str) -> dict[str, Any]:
        """预览；找不到旧版数据抛 LegacyImportError。"""
        from kacore.legacy_import.staging import inspect_legacy_install

        self._require_data_dir()
        return await asyncio.to_thread(inspect_legacy_install, path)

    async def start_legacy_import(self, path: str) -> dict[str, Any]:
        """开始导入（后台执行）；立即返回任务快照。"""
        from kacore.legacy_import.layout import locate

        data_dir = self._require_data_dir()
        job = self._legacy_import_job
        if job is not None and job.get("status") in {"running", "restarting"}:
            raise LegacyImportConflictError("An import is already in progress.")
        if not await self._library_is_empty():
            raise LegacyImportConflictError(
                "The current data folder already has documents. Old plugin data can only be "
                "imported into an empty library: choose an empty data folder in Preferences."
            )
        locate(path)  # 先同步校验一次路径，错误立即返回给界面
        self._legacy_import_job = {
            "status": "running",
            "stage": "preparing",
            "done": 0,
            "total": 0,
            "error": "",
            "source": path,
        }
        self._legacy_import_task = asyncio.create_task(self._run_legacy_import(path, data_dir))
        return dict(self._legacy_import_job)

    async def _run_legacy_import(self, path: str, data_dir: Path) -> None:
        from kacore.legacy_import.staging import stage_legacy_import

        job = self._legacy_import_job
        assert job is not None
        operation_id = self._runtime_events.start(
            category="system", operation="legacy_import", msg="old plugin data import started"
        )

        def _progress(stage: str, done: int, total: int) -> None:
            job.update(stage=stage, done=done, total=total)

        try:
            marker = await stage_legacy_import(path, data_dir, progress=_progress)
        except Exception as exc:  # noqa: BLE001 - 后台任务：记录并在状态里返回
            message = str(exc) if isinstance(exc, LegacyImportError) else f"Import failed: {exc}"
            logger.error("Old plugin data import failed: %s", message, exc_info=True)
            job.update(status="error", error=message)
            self._runtime_events.fail(
                operation_id=operation_id,
                category="system",
                operation="legacy_import",
                msg="old plugin data import failed",
                error=message,
            )
            return
        job.update(
            status="restarting",
            stage="restarting",
            summary=marker.get("summary"),
            attention=marker.get("attention"),
            dropped=marker.get("dropped"),
        )
        self._runtime_events.finish(
            operation_id=operation_id,
            category="system",
            operation="legacy_import",
            msg="Old plugin data is ready; it will be swapped in after the service restarts",
        )
        restart = await self.restart_service()  # type: ignore[attr-defined]
        if restart.get("status") != "restarting":
            job.update(
                status="staged",
                stage="staged",
                error="Restart the service manually to finish the import.",
            )

    async def get_legacy_import_status(self) -> dict[str, Any]:
        from kacore.legacy_import.apply import legacy_import_state

        data_dir = self._require_data_dir()
        state = await asyncio.to_thread(legacy_import_state, data_dir)
        return {
            "job": dict(self._legacy_import_job) if self._legacy_import_job else None,
            "library_empty": await self._library_is_empty(),
            **state,
        }

    async def discard_legacy_import(self) -> dict[str, Any]:
        from kacore.legacy_import.apply import discard_pending_legacy_import

        data_dir = self._require_data_dir()
        job = self._legacy_import_job
        if job is not None and job.get("status") in {"running", "restarting"}:
            raise LegacyImportConflictError(
                "The import is in progress and cannot be discarded yet."
            )
        discarded = await asyncio.to_thread(discard_pending_legacy_import, data_dir)
        self._legacy_import_job = None
        return {"status": "ok", "discarded": discarded}


__all__ = ["LegacyImportApiMixin", "LegacyImportConflictError"]
