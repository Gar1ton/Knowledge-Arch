"""外部论文入库（managers 层·3.0.0）：把回答里引用的外部论文放进用户的集合。

为何存在：联网补充只把外部文献的段落临时用在回答里；用户想长期保留时点来源栏的「入库」。入库做四件事：
1. 找 PDF：只试这次运行账本里已经记下的链接（`paper_import.pdf_candidates`，用户 11-23 选
   「不再调用模型」）。下载交给开着的、能下载文件的工具（`ToolBox.fetch_file`），核心不写工具名。
2. 入库：走现有的上传管线（组合根注入的 `register`，
   即 `KnowledgeRepositoryApi.register_document`），
   切块、向量、图谱待建都和手动上传一样；文件名按模板（`paper_import.paper_filename`）。
3. 书目：照常写进文档的 local_meta（title / creators / year / venue / doi / url），
   用户之后照常可以改。
4. 记账：`paper_imports` 一行，含出处（对话、回答、问题、工具、调用、位置）与下载的链接、文件指纹。

找不到 PDF 时记成「待补 PDF」：书目和出处先记下，给出能在浏览器打开的链接（DOI 链接、原文页、试过的
链接），用户自己下载后用 `attach_pdf` 补上，按同一个文件名入库。

契约：
- `import_citation` 对同一条引用（capture_id + ord）只建一条记录：已入库且文档还在时原样返回；
  待补 PDF 时重试下载。目标集合必须是本地、可写的集合。
- 下载失败不抛给调用方，记成待补 PDF（tried 里写原因）；
  记录、集合不存在等参数错误抛 ValueError。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.domain.models import DocumentOrigin
from kacore.domain.paper_import import (
    IMPORT_DISMISSED,
    IMPORT_IMPORTED,
    IMPORT_PENDING_PDF,
    ImportRecord,
    clean_filename,
    doi_key,
    paper_filename,
    pdf_candidates,
)

if TYPE_CHECKING:
    from kacore.adapters.tools.registry import ToolBox
    from kacore.config import PaperImportConfig
    from kacore.domain.models import Collection
    from kacore.domain.tool_calls import Capture, Citation, ToolCall
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("PaperImporter")

RegisterFn = Callable[..., Awaitable[str]]
PDF_CONTENT_TYPE = "application/pdf"
_PDF_MAGIC = b"%PDF-"


class PaperImporter:
    def __init__(
        self,
        *,
        store: SourceDocumentStore,
        toolbox: ToolBox,
        register: RegisterFn,
        staging_dir: Path,
        config_provider: Callable[[], PaperImportConfig],
    ) -> None:
        self._store = store
        self._toolbox = toolbox
        self._register = register
        self._staging = staging_dir
        self._config = config_provider

    # ── 入库框 ──────────────────────────────────────────────

    async def preview(self, capture_id: str, ord_: int) -> dict[str, Any]:
        """入库框要显示的：书目、建议文件名、会试几个链接、出处、这条引用已有的入库记录。"""
        capture, calls, citation = await self._load(capture_id, ord_)
        existing = await self._existing(capture_id, ord_)
        return {
            "fields": citation.fields,
            "filename": existing.filename if existing else self.suggest_filename(citation.fields),
            "candidates": pdf_candidates(citation, calls),
            "source": _source(capture, citation, calls),
            "existing": existing,
        }

    def suggest_filename(self, fields: dict[str, Any]) -> str:
        return paper_filename(fields, self._config().filename_template)

    # ── 入库 ────────────────────────────────────────────────

    async def import_citation(self, capture_id: str, ord_: int, *, coll_key: str,
                              filename: str = "") -> ImportRecord:
        capture, calls, citation = await self._load(capture_id, ord_)
        await self._require_target(coll_key)
        existing = await self._existing(capture_id, ord_)
        if existing and existing.status == IMPORT_IMPORTED \
                and await self._store.get_document(existing.doc_id) is not None:
            return existing
        now = _now()
        record = existing or ImportRecord(
            import_id=f"imp_{uuid.uuid4().hex[:16]}", capture_id=capture_id, ord=ord_,
            fields=dict(citation.fields), filename="", target=coll_key, created_at=now)
        record.filename = clean_filename(filename) if filename.strip() \
            else self.suggest_filename(citation.fields)
        record.target, record.status, record.updated_at = coll_key, IMPORT_PENDING_PDF, now
        record.source = _source(capture, citation, calls)
        record.tried = []
        for url in pdf_candidates(citation, calls):
            try:
                path, tool = await self._toolbox.fetch_file(url)
            except Exception as exc:  # noqa: BLE001 - 换下一个链接；原因留给用户看
                record.tried.append({"url": url, "error": str(exc)[:300]})
                continue
            record.tried.append({"url": url, "error": "", "tool": tool})
            return await self._ingest(record, path, pdf_url=url)
        await self._store.save_import(record)
        logger.info("Import %s is waiting for a PDF (%d links tried)",
                    record.import_id, len(record.tried))
        return record

    async def attach_pdf(self, import_id: str, path: Path) -> ImportRecord:
        """给待补 PDF 的记录补上用户下载的文件，按记录里的文件名入库。"""
        record = await self._store.get_import(import_id)
        if record is None:
            raise ValueError(f"import not found: {import_id}")
        if record.status != IMPORT_PENDING_PDF:
            raise ValueError(f"import {import_id} is not waiting for a PDF")
        if not await asyncio.to_thread(_is_pdf, path):
            raise ValueError("the file is not a PDF")
        await self._require_target(record.target)
        return await self._ingest(record, path, pdf_url="")

    async def dismiss(self, import_id: str) -> ImportRecord:
        """从待补清单里删掉（记录保留，状态改为 dismissed）。"""
        record = await self._store.get_import(import_id)
        if record is None:
            raise ValueError(f"import not found: {import_id}")
        if record.status == IMPORT_PENDING_PDF:
            record.status, record.updated_at = IMPORT_DISMISSED, _now()
            await self._store.save_import(record)
        return record

    # ── 内部 ────────────────────────────────────────────────

    async def _ingest(self, record: ImportRecord, pdf: Path, *, pdf_url: str) -> ImportRecord:
        """复制成目标文件名 → 现有上传管线 → 指定集合 → 写书目 → 记账。"""
        collection = await self._require_target(record.target)
        work = self._staging / record.import_id
        staged = work / record.filename
        try:
            await asyncio.to_thread(_copy, pdf, staged)
            size, sha = await asyncio.to_thread(_size_and_sha, staged)
            doc_id = await self._register(
                title=record.filename, file_path=str(staged), content_type=PDF_CONTENT_TYPE,
                size_bytes=size, content_hash=sha, collection=collection.name)
        finally:
            await asyncio.to_thread(shutil.rmtree, work, True)
        await self._store.set_document_collections(doc_id, [collection.coll_key])
        await self._write_biblio(doc_id, record.fields)
        record.status, record.doc_id = IMPORT_IMPORTED, doc_id
        record.pdf_url, record.pdf_sha256, record.updated_at = pdf_url, sha, _now()
        await self._store.save_import(record)
        logger.info("Imported %s as %s (%s)", record.import_id, doc_id, record.filename)
        return record

    async def _write_biblio(self, doc_id: str, fields: dict[str, Any]) -> None:
        """书目字段写进 local_meta（与文档界面手填的同名同形）；
        上传时从文件名猜的作者 / 年份被覆盖。"""
        doc = await self._store.get_document(doc_id)
        if doc is None:
            return
        creators = fields.get("creators")
        biblio = {
            "title": str(fields.get("title") or ""),
            "creators": list(creators) if isinstance(creators, list) else [],
            "year": str(fields.get("year") or ""),
            "venue": str(fields.get("venue") or ""),
            "doi": doi_key(str(fields.get("identifier") or "")),
            "url": str(fields.get("url") or ""),
        }
        doc.local_meta = {**(doc.local_meta or {}), **{k: v for k, v in biblio.items() if v}}
        if biblio["title"]:
            doc.title = biblio["title"]
        doc.updated_at = datetime.now(timezone.utc)
        await self._store.update_document(doc)

    async def _load(self, capture_id: str, ord_: int) -> tuple[Capture, list[ToolCall], Citation]:
        capture = await self._store.get_capture(capture_id)
        if capture is None:
            raise ValueError(f"capture not found: {capture_id}")
        citation = next((c for c in await self._store.list_citations(capture_id)
                         if c.ord == ord_), None)
        if citation is None:
            raise ValueError(f"source E{ord_} not found in {capture_id}")
        return capture, await self._store.list_tool_calls(capture_id), citation

    async def _existing(self, capture_id: str, ord_: int) -> ImportRecord | None:
        rows = [r for r in await self._store.list_imports(capture_id=capture_id)
                if r.ord == ord_ and r.status != IMPORT_DISMISSED]
        return rows[0] if rows else None

    async def _require_target(self, coll_key: str) -> Collection:
        collection = await self._store.get_collection(coll_key) if coll_key else None
        if collection is None:
            raise ValueError(f"collection not found: {coll_key}")
        if collection.origin is not DocumentOrigin.LOCAL or collection.read_only:
            raise ValueError("papers can only be imported into a local collection")
        return collection


def _source(capture: Capture, citation: Citation, calls: list[ToolCall]) -> dict[str, Any]:
    """出处：入库时从账本抄下来，之后账本怎么变都不影响。"""
    call = next((c for c in calls if c.call_id == citation.call_id), None)
    return {
        "conversation_id": capture.conv_id, "msg_idx": capture.msg_idx,
        "question": capture.question, "captured_at": capture.captured_at,
        "source_id": f"E{citation.ord}", "tool": call.tool if call else "",
        "call_label": f"c{call.ord}" if call else "", "locator": citation.locator,
        "page": citation.page,
    }


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(1024).lstrip().startswith(_PDF_MAGIC)
    except OSError:
        return False


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _size_and_sha(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while block := fh.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["PDF_CONTENT_TYPE", "PaperImporter"]
