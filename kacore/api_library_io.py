"""外部论文入库的 api 门面 mixin（3.0.0）：来源栏的入库框、文档栏的「待补 PDF」清单、
文档信息栏的出处。

只做门面：入库流程在 `managers/paper_importer.py`，这里负责参数校验、把记录转成界面用的 JSON
（目标集合名、能在浏览器打开的链接），以及「库里已有这篇」的提示（按 DOI，与联网补充跳过已在库
文献用同一份判断）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.domain.models import DocumentOrigin
from kacore.domain.paper_import import (
    IMPORT_IMPORTED,
    IMPORT_PENDING_PDF,
    IMPORT_STATUSES,
    ImportRecord,
    browser_links,
    doi_key,
)

if TYPE_CHECKING:
    from kacore.domain.paper_import import PushRecord
    from kacore.managers.paper_importer import PaperImporter
    from kacore.managers.zotero_pusher import ZoteroPusher
    from kacore.repository.source_store.base import SourceDocumentStore


class LibraryIoApiMixin:
    """组合进 KnowledgeRepositoryApi。依赖：_source_store、_paper_importer、_library_dois()。"""

    _source_store: SourceDocumentStore
    _paper_importer: PaperImporter | None = None
    _zotero_pusher: ZoteroPusher | None = None

    def attach_paper_importer(self, importer: PaperImporter) -> None:
        """组合根注入 PaperImporter（依赖 api.register_document，故在 api 构造后注入）。"""
        self._paper_importer = importer

    def attach_zotero_pusher(self, pusher: ZoteroPusher) -> None:
        """组合根注入 ZoteroPusher（3.0.0 推送）。"""
        self._zotero_pusher = pusher

    def _importer(self) -> PaperImporter:
        if self._paper_importer is None:
            raise RuntimeError("paper import is not configured")
        return self._paper_importer

    # ── 入库框 ──────────────────────────────────────────────

    async def import_preview(self, capture_id: str, ord_: int) -> dict[str, Any]:
        """入库框：书目、建议文件名、会试的链接数、出处、可选的本地集合、库里是否已有这篇。"""
        preview = await self._importer().preview(capture_id, ord_)
        existing: ImportRecord | None = preview.pop("existing")
        doi = doi_key(str(preview["fields"].get("identifier") or ""))
        in_library = None
        if doi:
            doc_id = (await self._library_dois()).get(doi)  # type: ignore[attr-defined]
            doc = await self._source_store.get_document(doc_id) if doc_id else None
            if doc is not None:
                in_library = {"doc_id": doc.doc_id, "title": doc.title}
        return {
            **preview,
            "candidates": len(preview["candidates"]),
            "targets": await self._local_targets(),
            "zotero_targets": await self._zotero_targets(),
            "existing": await self._import_json(existing) if existing else None,
            "in_library": in_library,
        }

    async def import_external_source(self, capture_id: str, ord_: int, *, collection_key: str,
                                     filename: str = "") -> dict[str, Any]:
        record = await self._importer().import_citation(
            capture_id, ord_, coll_key=collection_key, filename=filename)
        out = await self._import_json(record)
        out["push"] = await self.document_push(record.doc_id) if record.doc_id else None
        return out

    # ── Zotero 推送 ─────────────────────────────────────────

    async def push_preview(self, doc_id: str, collection_key: str = "") -> dict[str, Any]:
        """推送框：可选的 Zotero 集合、这篇的推送记录；选了集合时给出文件会移到哪、附件 path。
        前提不满足时 problem 写原因（不抛），界面据此提示。"""
        from kacore.managers.zotero_pusher import PushError

        out: dict[str, Any] = {"targets": await self._zotero_targets(),
                               "push": await self.document_push(doc_id), "plan": None,
                               "problem": ""}
        if self._zotero_pusher is None:
            out["problem"] = "Zotero push is not configured"
        elif collection_key:
            try:
                plan = await self._zotero_pusher.plan(doc_id, collection_key)
                out["plan"] = {"dest": str(plan.dest), "folder": str(plan.folder),
                               "path": plan.path, "filename": plan.dest.name}
            except PushError as exc:
                out["problem"] = str(exc)
        return out

    async def push_document(self, doc_id: str, collection_key: str,
                            filename: str = "") -> dict[str, Any]:
        if self._zotero_pusher is None:
            raise RuntimeError("Zotero push is not configured")
        record = await self._zotero_pusher.push(doc_id, collection_key, filename=filename)
        return _push_json(record)

    async def document_push(self, doc_id: str) -> dict[str, Any] | None:
        record = await self._source_store.get_push(doc_id)
        return _push_json(record) if record else None

    # ── 待补 PDF ────────────────────────────────────────────

    async def list_paper_imports(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in IMPORT_STATUSES:
            raise ValueError(f"unknown status: {status}")
        return [await self._import_json(r)
                for r in await self._source_store.list_imports(status=status)]

    async def attach_import_pdf(self, import_id: str, path: Path) -> dict[str, Any]:
        return await self._import_json(await self._importer().attach_pdf(import_id, path))

    async def dismiss_paper_import(self, import_id: str) -> dict[str, Any]:
        return await self._import_json(await self._importer().dismiss(import_id))

    # ── 文档信息栏 ──────────────────────────────────────────

    async def document_import_origin(self, doc_id: str) -> dict[str, Any] | None:
        """这篇文档是从哪次联网补充入库的；不是入库来的返回 None。"""
        rows = await self._source_store.list_imports(doc_id=doc_id)
        return await self._import_json(rows[0]) if rows else None

    async def capture_imports(self, capture_id: str) -> dict[int, dict[str, Any]]:
        """来源栏每条来源的入库状态：ord → {import_id, status, doc_id}（删掉的不算）。"""
        out: dict[int, dict[str, Any]] = {}
        for r in await self._source_store.list_imports(capture_id=capture_id):
            if r.ord not in out and r.status in (IMPORT_PENDING_PDF, IMPORT_IMPORTED):
                out[r.ord] = {"import_id": r.import_id, "status": r.status, "doc_id": r.doc_id}
        return out

    # ── 内部 ────────────────────────────────────────────────

    async def _local_targets(self) -> list[dict[str, str]]:
        """可入库的集合：本地、可写；path 用「 › 」连接父子集合名。"""
        collections = await self._source_store.list_collections()
        by_key = {c.coll_key: c for c in collections}

        def path_of(key: str) -> str:
            names, seen = [], set()
            while key and key in by_key and key not in seen:
                seen.add(key)
                names.append(by_key[key].name)
                key = by_key[key].parent_key
            return " › ".join(reversed(names))

        rows = [{"coll_key": c.coll_key, "path": path_of(c.coll_key)} for c in collections
                if c.origin is DocumentOrigin.LOCAL and not c.read_only]
        return sorted(rows, key=lambda r: r["path"].casefold())

    async def _zotero_targets(self) -> list[dict[str, str]]:
        """可推送的集合：Zotero 同步来的集合（不含「未归档」合成集合）；配了推送时才列。"""
        if self._zotero_pusher is None:
            return []
        collections = await self._source_store.list_collections()
        by_key = {c.coll_key: c for c in collections}

        def path_of(key: str) -> str:
            names, seen = [], set()
            while key and key in by_key and key not in seen:
                seen.add(key)
                names.append(by_key[key].name)
                key = by_key[key].parent_key
            return " › ".join(reversed(names))

        rows = [{"coll_key": c.coll_key, "path": path_of(c.coll_key)} for c in collections
                if c.origin is DocumentOrigin.ZOTERO and c.zotero_collection_key]
        return sorted(rows, key=lambda r: r["path"].casefold())

    async def _import_json(self, record: ImportRecord) -> dict[str, Any]:
        target = await self._source_store.get_collection(record.target)
        return {
            "import_id": record.import_id, "capture_id": record.capture_id, "ord": record.ord,
            "source_id": f"E{record.ord}", "fields": record.fields, "filename": record.filename,
            "target": record.target, "target_name": target.name if target else "",
            "status": record.status, "doc_id": record.doc_id, "pdf_url": record.pdf_url,
            "pdf_sha256": record.pdf_sha256, "source": record.source, "tried": record.tried,
            "links": browser_links(record.fields, record.tried)
            if record.status == IMPORT_PENDING_PDF else [],
            "created_at": record.created_at, "updated_at": record.updated_at,
        }


def _push_json(record: PushRecord) -> dict[str, Any]:
    return {"doc_id": record.doc_id, "status": record.status, "library_id": record.library_id,
            "collection_key": record.collection_key, "attachment_key": record.attachment_key,
            "path": record.path, "file_path": record.file_path, "message": record.message,
            "pushed_at": record.pushed_at, "claimed_at": record.claimed_at}


__all__ = ["LibraryIoApiMixin"]
