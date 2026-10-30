"""FigureRepository 的内存实现（混入 InMemorySourceDocumentStore，供接口对换测试）。"""

from __future__ import annotations

import copy
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.figures import DocumentFigure


class InMemoryFigureMixin:
    """深拷贝进出，与 SQLite 实现行为一致。文档删除时由 InMemorySourceDocumentStore 清理。"""

    _figures: dict[str, list[DocumentFigure]]

    async def replace_document_figures(self, doc_id: str, figures: list[DocumentFigure]) -> None:
        self._figures[doc_id] = sorted(
            (copy.deepcopy(f) for f in figures), key=lambda f: (f.page, f.ordinal)
        )

    async def list_document_figures(self, doc_id: str) -> list[DocumentFigure]:
        return copy.deepcopy(self._figures.get(doc_id, []))

    async def list_figures(
        self, *, statuses: set[str] | None = None, doc_ids: list[str] | None = None
    ) -> list[DocumentFigure]:
        wanted = None if doc_ids is None else set(doc_ids)
        found = [
            copy.deepcopy(f)
            for doc_id in sorted(self._figures)
            if wanted is None or doc_id in wanted
            for f in self._figures[doc_id]
            if statuses is None or f.status in statuses
        ]
        return found

    async def update_figure_result(self, figure: DocumentFigure) -> bool:
        for stored in self._figures.get(figure.doc_id, []):
            if stored.figure_id == figure.figure_id:
                stored.status = figure.status
                stored.description = figure.description
                stored.model = figure.model
                stored.prompt_version = figure.prompt_version
                stored.image_hash = figure.image_hash
                stored.error = figure.error
                stored.updated_at = figure.updated_at
                return True
        return False

    async def count_figures_by_status(self, doc_ids: list[str] | None = None) -> dict[str, int]:
        wanted = None if doc_ids is None else set(doc_ids)
        return dict(Counter(
            f.status
            for doc_id, figures in self._figures.items()
            if wanted is None or doc_id in wanted
            for f in figures
        ))


__all__ = ["InMemoryFigureMixin"]
