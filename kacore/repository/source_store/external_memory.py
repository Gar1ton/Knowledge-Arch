"""外部来源账本与推送账本的内存实现（混入 InMemorySourceDocumentStore，供接口对换测试）。"""
from __future__ import annotations

import copy
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


class InMemoryExternalMixin:
    """深拷贝进出，行为与 SQLite 实现一致。属性由 InMemorySourceDocumentStore 初始化。"""

    _web_captures: dict[str, Capture]
    _web_sources: dict[str, StoredSource]
    _web_hits: dict[tuple[str, str, str, str], CaptureHit]
    _web_citations: dict[str, list[Citation]]
    _web_fields: dict[tuple[str, str, str], FieldOrigin]
    _pushes: dict[str, PushEntry]

    async def save_capture(
        self,
        capture: Capture,
        sources: list[StoredSource],
        hits: list[CaptureHit],
        fields: list[FieldOrigin],
    ) -> None:
        if capture.capture_id in self._web_captures:
            raise ValueError(f"duplicate capture_id {capture.capture_id}")
        self._web_captures[capture.capture_id] = copy.deepcopy(capture)
        for s in sources:
            old = self._web_sources.get(s.source_id)
            new = copy.deepcopy(s)
            if old is not None:
                new.first_seen = old.first_seen
                if not new.placement:
                    new.placement = old.placement
            self._web_sources[s.source_id] = new
        for h in hits:
            self._web_hits[(h.capture_id, h.source_id, h.engine, h.query)] = copy.deepcopy(h)
        for f in fields:
            self._web_fields[(f.source_id, f.field, f.engine)] = copy.deepcopy(f)

    async def save_citations(self, capture_id: str, citations: list[Citation]) -> None:
        self._web_citations[capture_id] = [copy.deepcopy(c) for c in citations]

    async def update_placements(self, placements: dict[str, dict[str, object]]) -> None:
        for sid, placement in placements.items():
            if sid in self._web_sources:
                self._web_sources[sid].placement = copy.deepcopy(placement)

    async def get_capture(self, capture_id: str) -> Capture | None:
        found = self._web_captures.get(capture_id)
        return copy.deepcopy(found) if found else None

    async def list_captures(self, conv_id: str) -> list[Capture]:
        rows = [c for c in self._web_captures.values() if c.conv_id == conv_id]
        return copy.deepcopy(sorted(rows, key=lambda c: (c.captured_at, c.capture_id)))

    async def list_capture_hits(self, capture_ids: list[str]) -> list[CaptureHit]:
        order = {cid: i for i, cid in enumerate(capture_ids)}
        rows = [h for h in self._web_hits.values() if h.capture_id in order]
        return copy.deepcopy(sorted(rows, key=lambda h: (order[h.capture_id], h.engine, h.rank)))

    async def list_citations(self, capture_id: str) -> list[Citation]:
        return copy.deepcopy(sorted(self._web_citations.get(capture_id, []), key=lambda c: c.ord))

    async def get_sources(self, source_ids: list[str]) -> dict[str, StoredSource]:
        return {sid: copy.deepcopy(self._web_sources[sid]) for sid in source_ids
                if sid in self._web_sources}

    async def list_source_fields(self, source_id: str) -> list[FieldOrigin]:
        rows = [f for f in self._web_fields.values() if f.source_id == source_id]
        return copy.deepcopy(sorted(rows, key=lambda f: (f.field, f.engine)))

    async def upsert_push(self, entry: PushEntry) -> None:
        self._pushes[entry.doc_id] = copy.deepcopy(entry)

    async def get_push(self, doc_id: str) -> PushEntry | None:
        found = self._pushes.get(doc_id)
        return copy.deepcopy(found) if found else None

    async def find_push_by_item(self, library_id: str, item_key: str) -> PushEntry | None:
        found = next((p for p in self._pushes.values()
                      if p.library_id == library_id and p.item_key == item_key), None)
        return copy.deepcopy(found) if found else None

    async def list_pushes(self, statuses: set[str] | None = None) -> list[PushEntry]:
        rows = [p for p in self._pushes.values() if statuses is None or p.status in statuses]
        return copy.deepcopy(sorted(rows, key=lambda p: (p.updated_at, p.doc_id)))


__all__ = ["InMemoryExternalMixin"]
