"""外部来源账本与 Zotero 推送账本的仓储契约（ABC）。

SourceDocumentStore 继承本接口；实现见 external_sqlite / external_memory（与图空位一样单列，
因为 source_store 的 base / sqlite / memory 都已过 600 行）。契约是唯一真相源。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.domain.external_ledger import (
        Capture,
        CaptureHit,
        Citation,
        FieldOrigin,
        PushEntry,
        StoredSource,
    )


class ExternalSourceRepository(ABC):
    """回答里外部文献的出处账本（迁移 029）。"""

    @abstractmethod
    async def save_capture(
        self,
        capture: Capture,
        sources: list[StoredSource],
        hits: list[CaptureHit],
        fields: list[FieldOrigin],
    ) -> None:
        """一个事务内写入一次联网补充：capture → 文献 upsert → 命中 → 字段出处 upsert。

        文献已存在时更新 trust / aliases / meta / reference_ids / checked_at，保留 first_seen
        与已有的 placement（placement 为空时不覆盖）。capture_id 重复时抛异常
        （调用方负责生成唯一编号）。
        """
        ...

    @abstractmethod
    async def save_citations(self, capture_id: str, citations: list[Citation]) -> None:
        """整体替换某次联网补充的引用段落（回答重写时整批替换）。"""
        ...

    @abstractmethod
    async def update_placements(self, placements: dict[str, dict[str, object]]) -> None:
        """写回按用户集合树的归类（source_id → {path / new_direction / reason}）；未知 id 忽略。"""
        ...

    @abstractmethod
    async def get_capture(self, capture_id: str) -> Capture | None:
        """取一次联网补充；不存在返回 None。"""
        ...

    @abstractmethod
    async def list_captures(self, conv_id: str) -> list[Capture]:
        """某对话的全部联网补充，按 captured_at 升序。"""
        ...

    @abstractmethod
    async def list_capture_hits(self, capture_ids: list[str]) -> list[CaptureHit]:
        """若干次联网补充的命中，按 (capture 顺序, engine, rank) 排。"""
        ...

    @abstractmethod
    async def list_citations(self, capture_id: str) -> list[Citation]:
        """某次联网补充的引用段落，按 ord 升序。"""
        ...

    @abstractmethod
    async def get_sources(self, source_ids: list[str]) -> dict[str, StoredSource]:
        """按 id 取文献；不存在的 id 不出现在结果里。"""
        ...

    @abstractmethod
    async def list_source_fields(self, source_id: str) -> list[FieldOrigin]:
        """某篇文献的字段级出处，按 (field, engine) 排。"""
        ...


class ZoteroPushRepository(ABC):
    """本地文档推送回 Zotero 的账本（迁移 030）。"""

    @abstractmethod
    async def upsert_push(self, entry: PushEntry) -> None:
        """按 doc_id 写入或整体更新一条推送记录。文档删除时由外键级联删除。"""
        ...

    @abstractmethod
    async def get_push(self, doc_id: str) -> PushEntry | None:
        """取某文档的推送记录；没有返回 None。"""
        ...

    @abstractmethod
    async def find_push_by_item(self, library_id: str, item_key: str) -> PushEntry | None:
        """按 Zotero 条目找推送记录（拉取时认领用）；没有返回 None。"""
        ...

    @abstractmethod
    async def list_pushes(self, statuses: set[str] | None = None) -> list[PushEntry]:
        """按状态筛选推送记录（None 表示全部），按 updated_at 升序。"""
        ...


__all__ = ["ExternalSourceRepository", "ZoteroPushRepository"]
