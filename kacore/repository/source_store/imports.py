"""外部论文入库账本的仓储契约（ABC，迁移 030_paper_imports）。

SourceDocumentStore 继承本接口；实现见 imports_sqlite / imports_memory（与联网账本一样单列，
因为 source_store 的 base / sqlite / memory 都已过 600 行）。契约是唯一真相源。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.paper_import import ImportRecord


class ImportLedgerRepository(ABC):
    @abstractmethod
    async def save_import(self, record: ImportRecord) -> None:
        """新建或整条覆盖一条入库记录（按 import_id）。调用方负责 updated_at。"""
        ...

    @abstractmethod
    async def get_import(self, import_id: str) -> ImportRecord | None:
        """取一条入库记录；不存在返回 None。"""
        ...

    @abstractmethod
    async def list_imports(
        self, *, status: str | None = None, capture_id: str | None = None,
        doc_id: str | None = None,
    ) -> list[ImportRecord]:
        """按条件列出入库记录（条件为 None 表示不限），按 updated_at 降序。"""
        ...


__all__ = ["ImportLedgerRepository"]
