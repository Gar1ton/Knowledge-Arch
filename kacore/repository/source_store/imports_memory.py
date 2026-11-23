"""外部论文入库账本的内存实现（混入 InMemorySourceDocumentStore，供接口对换测试）。"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.paper_import import ImportRecord


class InMemoryImportLedgerMixin:
    """深拷贝进出，行为与 SQLite 实现一致。属性由 InMemorySourceDocumentStore 初始化。"""

    _paper_imports: dict[str, ImportRecord]

    async def save_import(self, record: ImportRecord) -> None:
        self._paper_imports[record.import_id] = copy.deepcopy(record)

    async def get_import(self, import_id: str) -> ImportRecord | None:
        record = self._paper_imports.get(import_id)
        return copy.deepcopy(record) if record else None

    async def list_imports(
        self, *, status: str | None = None, capture_id: str | None = None,
        doc_id: str | None = None,
    ) -> list[ImportRecord]:
        rows = [r for r in self._paper_imports.values()
                if (status is None or r.status == status)
                and (capture_id is None or r.capture_id == capture_id)
                and (doc_id is None or r.doc_id == doc_id)]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return [copy.deepcopy(r) for r in rows]


__all__ = ["InMemoryImportLedgerMixin"]
