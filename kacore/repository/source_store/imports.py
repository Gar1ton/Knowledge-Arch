"""外部论文入库与 Zotero 推送两本账的仓储契约（ABC，迁移 030_paper_imports）。

SourceDocumentStore 继承本接口；实现见 imports_sqlite / imports_memory（与联网账本一样单列，
因为 source_store 的 base / sqlite / memory 都已过 600 行）。契约是唯一真相源。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.paper_import import ImportRecord, PushRecord


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

    @abstractmethod
    async def save_push(self, record: PushRecord) -> None:
        """新建或整条覆盖一篇文档的推送记录（按 doc_id；一篇文档只有一条）。"""
        ...

    @abstractmethod
    async def get_push(self, doc_id: str) -> PushRecord | None:
        """取一篇文档的推送记录；没推送过返回 None。"""
        ...

    @abstractmethod
    async def list_pushes(self, status: str | None = None) -> list[PushRecord]:
        """按状态列出推送记录（None 表示不限），按 pushed_at 降序。"""
        ...

    @abstractmethod
    async def push_claims(self) -> dict[str, str]:
        """Zotero 拉取认领用：attachment_key → doc_id，只含已推送（pushed / claimed）且有
        attachment_key 的记录。没有推送过时为空 dict。"""
        ...


__all__ = ["ImportLedgerRepository"]
