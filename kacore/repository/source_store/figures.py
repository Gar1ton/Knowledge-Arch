"""图表空位仓储契约（ABC）。SourceDocumentStore 继承本接口；实现见 figures_sqlite / figures_memory。

单列文件是因为 source_store 的 base / sqlite / memory 都已过 600 行；契约仍是唯一真相源。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.figures import DocumentFigure


class FigureRepository(ABC):
    """文档图表空位（document_figures）的读写契约。"""

    @abstractmethod
    async def replace_document_figures(self, doc_id: str, figures: list[DocumentFigure]) -> None:
        """整体替换某文档的全部图空位（同一事务内先删后插）。

        调用方负责先用 `merge_figure_descriptions` 把旧描述接到新空位上；本方法只做持久化。
        文档删除时由外键级联删除其空位。
        """
        ...

    @abstractmethod
    async def list_document_figures(self, doc_id: str) -> list[DocumentFigure]:
        """某文档的图空位，按 (page, ordinal) 升序；没有时返回空列表。"""
        ...

    @abstractmethod
    async def list_figures(
        self, *, statuses: set[str] | None = None, doc_ids: list[str] | None = None
    ) -> list[DocumentFigure]:
        """按状态 / 文档筛选图空位（None 表示不筛），按 (doc_id, page, ordinal) 升序。"""
        ...

    @abstractmethod
    async def update_figure_result(self, figure: DocumentFigure) -> bool:
        """写回一张图的识图结果：status / description / model / prompt_version / image_hash /
        error / updated_at。空位不存在（文档已删或已重新抽取）返回 False，不报错。"""
        ...

    @abstractmethod
    async def count_figures_by_status(self, doc_ids: list[str] | None = None) -> dict[str, int]:
        """各状态的图数量（只含出现过的状态）；doc_ids=None 统计全部文档。"""
        ...


__all__ = ["FigureRepository"]
