"""把本地文档推送到 Zotero（managers 层·3.0.0）。

为何存在：用户 11-19 要求「可以将添加的文献 push 回 zotero 同步，并兼容 zotmoov」。11-20 定的做法：
- 只走「KA 放文件 + Web API」一条路：KA 按 ZotMoov 的目录规则把 PDF 放好，再用 Web API 建一条
  独立的 linked_file 附件（没有父条目，与用户现有的 165 个附件一样；Zotero 只给子附件自动改名，
  所以文件名保持用户的风格）。不上传到 Zotero 云存储：ZotMoov 默认不处理经同步到达的文件。
- 推送 = 移交：文件移出 KA 的制品包（不留副本），之后归 Zotero 管；KA 的切块、向量、笔记保留。
- 下次拉取按 attachment_key 认领原文档（`pipelines/zotero_sync_pipeline.py`），不重新切块和嵌入。

放到哪里（只看路径结构，不判断内容）：
- 这个集合里已有 linked_file 附件、且都在同一个文件夹 → 放那个文件夹；
- 否则 → ZotMoov 目录 / 集合路径（父集合名 / 子集合名，与 ZotMoov 按集合建子目录的规则一致）。
附件的 path 写成 `attachments:<相对 Zotero 链接附件根目录的路径>`。根目录取设置
`flow.zotero.base_dir`；空着时从任意一个能在磁盘上找到的已有附件推出（文件的绝对路径去掉它的
attachments: 相对部分）。

契约：
- `plan` 只读（给推送框预览）；条件不满足抛 PushError（原因写给用户看）。
- `push` 先移动文件再建附件；建附件失败时把文件移回原处，记一条 failed（不抛）。
  移回也失败时 message 写清文件现在在哪。条件不满足抛 PushError，什么都不动。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from kacore.adapters.zotero.web_api import ZoteroWebApiClient, current_key_identity
from kacore.adapters.zotero.zotmoov import (
    ATTACHMENTS_PREFIX,
    LINK_MODE_LINKED_FILE,
    ZotmoovResolver,
    attachments_relative,
)
from kacore.config import ZOTERO_STORAGE_ZOTMOOV
from kacore.domain.models import DocumentOrigin
from kacore.domain.paper_import import (
    PUSH_CLAIMED,
    PUSH_FAILED,
    PUSH_PUSHED,
    PushRecord,
    clean_filename,
    doi_key,
    windows_safe,
)

if TYPE_CHECKING:
    from kacore.config import ZoteroSyncConfig
    from kacore.domain.models import Collection, SourceDocument
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("ZoteroPusher")

LOCAL_LIBRARY_ID = "LOCAL"
PDF_CONTENT_TYPE = "application/pdf"


class PushError(ValueError):
    """推送的前提不满足（没配 ZotMoov 目录、key 没有写权限、文档不是本地的…）；消息给用户看。"""


@dataclass(frozen=True)
class PushPlan:
    doc_id: str
    collection_key: str      # Zotero 的集合 key（不含库前缀）
    folder: Path
    dest: Path
    path: str                # attachments:…


class ZoteroPusher:
    def __init__(
        self,
        *,
        store: SourceDocumentStore,
        config_provider: Callable[[], ZoteroSyncConfig],
        api_key_provider: Callable[[], str],
        client_factory: Callable[[str], Any] = ZoteroWebApiClient,
    ) -> None:
        self._store = store
        self._config = config_provider
        self._api_key = api_key_provider
        self._client_factory = client_factory

    # ── 公开 ────────────────────────────────────────────────

    async def check_target(self, coll_key: str) -> Collection:
        """推送目标可用：是 Zotero 集合、配了 ZotMoov 目录与 key（不联网，入库前先查一遍）。"""
        collection = await self._store.get_collection(coll_key)
        if collection is None or collection.origin is not DocumentOrigin.ZOTERO \
                or not collection.zotero_collection_key:
            raise PushError("the target is not a Zotero collection")
        self._root()
        if not self._api_key().strip():
            raise PushError("add a Zotero Web API key with write access in the Zotero node first")
        return collection

    async def plan(self, doc_id: str, coll_key: str, *, filename: str = "") -> PushPlan:
        doc = await self._require_local(doc_id)
        collection = await self.check_target(coll_key)
        root = self._root()
        base = await self._base_dir(collection.library_id, root)
        folder = await self._target_folder(collection, root, base)
        name = clean_filename(filename) if filename.strip() else await self._filename(doc)
        dest = _unique(folder / name, Path(doc.file_path))
        try:
            rel = dest.relative_to(base)
        except ValueError as exc:
            raise PushError(f"the ZotMoov folder {root} is not inside the Zotero base folder "
                            f"{base}; set the base folder in the Zotero node") from exc
        return PushPlan(doc_id, collection.zotero_collection_key, folder, dest,
                        ATTACHMENTS_PREFIX + PurePosixPath(*rel.parts).as_posix())

    async def push(self, doc_id: str, coll_key: str, *, filename: str = "") -> PushRecord:
        existing = await self._store.get_push(doc_id)
        if existing and existing.status in (PUSH_PUSHED, PUSH_CLAIMED):
            raise PushError("this document was already pushed to Zotero")
        plan = await self.plan(doc_id, coll_key, filename=filename)
        doc = await self._require_local(doc_id)
        client = self._client_factory(self._api_key().strip())
        identity = await asyncio.to_thread(lambda: current_key_identity(client.get_current_key()))
        if not identity["access"].get("write"):
            raise PushError(
                "this Zotero key has no write access; allow writing for it on zotero.org")
        record = PushRecord(doc_id, identity["user_id"], plan.collection_key, uuid.uuid4().hex,
                            path=plan.path, pushed_at=_now())
        source = Path(doc.file_path)
        await asyncio.to_thread(_move, source, plan.dest)
        try:
            item = await self._attachment_item(client, doc, plan)
            created = await asyncio.to_thread(
                client.create_items, identity["user_id"], [item], record.write_token)
        except Exception as exc:  # noqa: BLE001 - 失败写进账本，文件移回
            record.status, record.message = PUSH_FAILED, str(exc)[:500]
            try:
                await asyncio.to_thread(_move, plan.dest, source)
            except OSError as move_exc:
                record.message += f" (the file is still at {plan.dest}: {move_exc})"
                record.file_path = str(plan.dest)
            await self._store.save_push(record)
            logger.warning("Push of %s failed: %s", doc_id, record.message)
            return record
        record.attachment_key = str(created["key"])
        record.file_path = str(plan.dest)
        doc.file_path = str(plan.dest)
        doc.updated_at = datetime.now(timezone.utc)
        await self._store.update_document(doc)
        await self._store.save_push(record)
        logger.info("Pushed %s to Zotero as %s (%s)", doc_id, record.attachment_key, plan.path)
        return record

    # ── 内部 ────────────────────────────────────────────────

    def _root(self) -> Path:
        cfg = self._config()
        if cfg.storage_mode != ZOTERO_STORAGE_ZOTMOOV or not cfg.zotmoov_root.strip():
            raise PushError("pushing needs the ZotMoov storage mode and the ZotMoov folder "
                            "in the Zotero node")
        root = Path(cfg.zotmoov_root).expanduser()
        if not root.is_dir():
            raise PushError(f"the ZotMoov folder does not exist: {root}")
        return root

    async def _base_dir(self, library_id: str, root: Path) -> Path:
        configured = self._config().base_dir.strip()
        if configured:
            return Path(configured).expanduser()
        resolver = ZotmoovResolver(root)
        for att in await self._store.list_zotero_attachments(library_id):
            rel = attachments_relative(att.path)
            if att.link_mode != LINK_MODE_LINKED_FILE or not rel:
                continue
            found, _ = await asyncio.to_thread(resolver.resolve, att.path, att.filename)
            base = _strip_relative(found, rel) if found else None
            if base is not None:
                return base
        raise PushError("cannot work out the Zotero base folder from existing attachments; "
                        "set it in the Zotero node")

    async def _target_folder(self, collection: Collection, root: Path, base: Path) -> Path:
        """集合里已有附件都在同一个文件夹就用它；否则 ZotMoov 目录 / 集合路径。"""
        lib, zkey = collection.library_id, collection.zotero_collection_key
        items = set(await self._store.get_items_in_collections(lib, [zkey]))
        folders = {
            PurePosixPath(attachments_relative(a.path)).parent
            for a in await self._store.list_zotero_attachments(lib)
            if a.link_mode == LINK_MODE_LINKED_FILE and attachments_relative(a.path)
            and ((a.attachment_key in items and not a.parent_item_key)
                 or a.parent_item_key in items)
        }
        if len(folders) == 1:
            return base.joinpath(*next(iter(folders)).parts)
        names: list[str] = []
        key, seen = collection.coll_key, set()
        while key and key not in seen:
            seen.add(key)
            node = await self._store.get_collection(key)
            if node is None or node.origin is not DocumentOrigin.ZOTERO:
                break
            names.append(windows_safe(node.name, whole_name=True))
            key = node.parent_key
        return root.joinpath(*reversed(names))

    async def _filename(self, doc: SourceDocument) -> str:
        """入库来的用入库时定的文件名；上传的用上传时的文件名；都没有用标题。"""
        imports = await self._store.list_imports(doc_id=doc.doc_id)
        if imports:
            return imports[0].filename
        for att in await self._store.list_zotero_attachments(LOCAL_LIBRARY_ID,
                                                             doc.zotero_item_key):
            if att.attachment_key == doc.attachment_key and att.filename:
                return clean_filename(att.filename)
        return clean_filename(doc.title)

    async def _attachment_item(self, client: Any, doc: SourceDocument,
                               plan: PushPlan) -> dict[str, Any]:
        """按 Zotero 给的模板填（只填模板里有的键，不猜字段名）。"""
        template = await asyncio.to_thread(client.item_template, LINK_MODE_LINKED_FILE)
        doi = doi_key(str((doc.local_meta or {}).get("doi") or ""))
        values = {"title": plan.dest.name, "path": plan.path, "contentType": PDF_CONTENT_TYPE,
                  "collections": [plan.collection_key],
                  "url": f"https://doi.org/{doi}" if doi else ""}
        item = dict(template)
        item.update({k: v for k, v in values.items() if k in template})
        return item

    async def _require_local(self, doc_id: str) -> SourceDocument:
        doc = await self._store.get_document(doc_id)
        if doc is None:
            raise PushError(f"document not found: {doc_id}")
        if doc.origin is not DocumentOrigin.LOCAL:
            raise PushError("only local documents can be pushed to Zotero")
        if not doc.file_path or not Path(doc.file_path).is_file():
            raise PushError("the document's PDF file is missing")
        return doc


def _strip_relative(found: Path, rel: str) -> Path | None:
    """found 以 rel 的各段结尾（忽略大小写）时返回去掉这几段后的目录，否则 None。"""
    parts = PurePosixPath(rel).parts
    if len(found.parts) <= len(parts):
        return None
    tail = [p.casefold() for p in found.parts[-len(parts):]]
    if tail != [p.casefold() for p in parts]:
        return None
    return found.parents[len(parts) - 1]


def _unique(dest: Path, own: Path) -> Path:
    """目标已有同名文件时加 (2)、(3)…；不覆盖。own 是文档自己的文件（重推时可能就在那里）。"""
    if not dest.exists() or dest == own:
        return dest
    for i in range(2, 1000):
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not candidate.exists():
            return candidate
    raise PushError(f"too many files named {dest.name} in {dest.parent}")


def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["PushError", "PushPlan", "ZoteroPusher"]
