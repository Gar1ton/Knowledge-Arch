"""`.kaarch` 导入的「合并」（managers 层·3.0.0）：只加本机没有的东西。

规则（计划 5.2，用户 11-23 选默认「再带文档笔记」）：
- 本地集合按路径（父 › 子）对上；本机没有的建出来，已有的沿用。
- 本地文档按 content_hash 判断本机有没有；没有的拿文件里的原件走现有上传管线（register_document），
  再写回书目字段、标签与所在集合，再把它的文档笔记加进来。切块和向量在本机重算：两边的编号与
  Embedding 设置可能不同，不跨库拷贝。
- Zotero 文档跳过，交给下次拉取（两边都连同一个 Zotero 时自然会同步过来）。
- 对话记录不带（用户 11-23 选的默认）。
- 设置、密钥只补本机没填的键。
单篇失败不中断，记进结果的 failed。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from kacore.domain.models import DocumentOrigin
from kacore.domain.paper_import import windows_safe

if TYPE_CHECKING:
    from kacore.managers.kaarch import KaarchJob
    from kacore.repository.source_store.base import SourceDocumentStore
    from kacore.settings.base import SecretStore
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("Kaarch")

# 合并时写回的书目字段（与文档界面手填的字段同名）；处理版本等内部字段不带，由本机重新处理生成。
BIBLIO_KEYS = ("title", "creators", "year", "venue", "doi", "url", "abstract")


class LibraryWriter(Protocol):
    """合并要用的 api 方法（KnowledgeRepositoryApi 满足）。"""

    async def create_subcollection(
        self, name: str, parent_key: str = "", description: str = ""
    ) -> str: ...

    async def register_document(
        self, *, title: str, file_path: str, content_type: str, size_bytes: int,
        content_hash: str, collection: str, tags: list[str] | None = None,
    ) -> str: ...

    async def create_document_note(self, doc_id: str, content: str) -> dict[str, Any] | None: ...


async def merge_archive(
    archive: Path,
    manifest: dict[str, Any],
    secrets: dict[str, str] | None,
    job: KaarchJob,
    *,
    store: SourceDocumentStore,
    writer: LibraryWriter,
    settings: SettingsStore | None,
    secret_store: SecretStore | None,
    work: Path,
) -> dict[str, Any]:
    out: dict[str, Any] = {"collections_added": 0, "documents_added": 0,
                           "documents_present": 0, "zotero_skipped": 0, "notes_added": 0,
                           "settings_added": 0, "secrets_added": 0, "failed": []}
    parts = manifest["parts"]
    try:
        if "library" in parts:
            job.stage, job.progress = "read", 5
            db_path = work / "archive.db"
            await asyncio.to_thread(_extract, archive, "payload/database/knowledge_repository.db",
                                    db_path)
            snapshot = await asyncio.to_thread(_read_snapshot, db_path)
            keymap = await _merge_collections(snapshot["collections"], store, writer, out)
            await _merge_documents(archive, snapshot, keymap, store, writer, work, job, out)
        if "settings" in parts and settings is not None:
            with zipfile.ZipFile(archive) as zf:
                incoming = json.loads(zf.read("settings.json").decode("utf-8"))
            out["settings_added"] = _merge_settings(settings, incoming)
        if secrets and secret_store is not None:
            for name, value in secrets.items():
                if not secret_store.has_secret(name):
                    secret_store.set_secret(name, value)
                    out["secrets_added"] += 1
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
    return out


# ── 集合 ────────────────────────────────────────────────────


async def _merge_collections(rows: list[dict[str, str]], store: SourceDocumentStore,
                             writer: LibraryWriter, out: dict[str, Any]) -> dict[str, str]:
    """文件里的本地集合 coll_key → 本机 coll_key（按路径对上，没有的新建）。"""
    archive_paths = _paths({r["coll_key"]: (r["name"], r["parent_key"]) for r in rows})
    local = [c for c in await store.list_collections() if c.origin is DocumentOrigin.LOCAL]
    by_path = {path: key for key, path in _paths(
        {c.coll_key: (c.name, c.parent_key) for c in local}).items()}
    keymap: dict[str, str] = {}
    for key, path in sorted(archive_paths.items(), key=lambda kv: len(kv[1])):
        if path not in by_path:
            parent = by_path.get(path[:-1], "") if len(path) > 1 else ""
            by_path[path] = await writer.create_subcollection(path[-1], parent)
            out["collections_added"] += 1
        keymap[key] = by_path[path]
    return keymap


def _paths(tree: dict[str, tuple[str, str]]) -> dict[str, tuple[str, ...]]:
    """coll_key → (根名, …, 自己的名)；父不在这棵树里的当作顶层。"""
    out: dict[str, tuple[str, ...]] = {}
    for key in tree:
        names, seen, cur = [], set(), key
        while cur in tree and cur not in seen:
            seen.add(cur)
            names.append(tree[cur][0])
            cur = tree[cur][1]
        out[key] = tuple(reversed(names))
    return out


# ── 文档 ────────────────────────────────────────────────────


async def _merge_documents(archive: Path, snapshot: dict[str, Any], keymap: dict[str, str],
                           store: SourceDocumentStore, writer: LibraryWriter, work: Path,
                           job: KaarchJob, out: dict[str, Any]) -> None:
    present = {d.content_hash for d in await store.list_documents()}
    names = {c.coll_key: c.name for c in await store.list_collections()}
    docs = snapshot["documents"]
    out["zotero_skipped"] = snapshot["zotero_documents"]
    for i, doc in enumerate(docs, start=1):
        job.progress = 10 + int(85 * i / max(len(docs), 1))
        job.detail = doc["title"]
        if doc["content_hash"] in present:
            out["documents_present"] += 1
            continue
        try:
            new_id = await _add_document(archive, doc, snapshot, keymap, names, writer, work)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不中断合并
            logger.warning("Merge of %s failed: %s", doc["doc_id"], exc)
            out["failed"].append({"title": doc["title"], "error": str(exc)[:300]})
            continue
        await _restore_fields(store, new_id, doc, keymap)
        for content in snapshot["notes"].get(doc["doc_id"], []):
            if await writer.create_document_note(new_id, content):
                out["notes_added"] += 1
        present.add(doc["content_hash"])
        out["documents_added"] += 1


async def _add_document(archive: Path, doc: dict[str, Any], snapshot: dict[str, Any],
                        keymap: dict[str, str], names: dict[str, str], writer: LibraryWriter,
                        work: Path) -> str:
    filename = snapshot["filenames"].get(doc["attachment_key"]) or \
        windows_safe(doc["title"]) + ".pdf"
    target = work / "files" / doc["doc_id"] / Path(filename).name
    await asyncio.to_thread(_extract, archive, f"payload/library/{doc['doc_id']}/original.pdf",
                            target)
    keys = [keymap[k] for k in snapshot["memberships"].get(doc["doc_id"], []) if k in keymap]
    collection = names.get(keys[0], "") if keys else ""
    if not collection:
        collection = next((n for k, n in names.items() if k in keymap.values()), "default")
    return await writer.register_document(
        title=target.name, file_path=str(target), content_type=doc["content_type"],
        size_bytes=target.stat().st_size, content_hash=doc["content_hash"],
        collection=collection, tags=doc["tags"])


async def _restore_fields(store: SourceDocumentStore, doc_id: str, doc: dict[str, Any],
                          keymap: dict[str, str]) -> None:
    """标题、书目字段、标签、所在集合照文件里的写回。"""
    current = await store.get_document(doc_id)
    if current is None:
        return
    biblio = {k: v for k, v in (doc["local_meta"] or {}).items() if k in BIBLIO_KEYS and v}
    current.title = doc["title"] or current.title
    current.local_meta = {**(current.local_meta or {}), **biblio}
    current.tags = list(doc["tags"])
    current.updated_at = datetime.now(timezone.utc)
    await store.update_document(current)
    keys = [keymap[k] for k in doc["memberships"] if k in keymap]
    if keys:
        await store.set_document_collections(doc_id, keys)


# ── 设置 ────────────────────────────────────────────────────


def _merge_settings(settings: SettingsStore, incoming: dict[str, Any]) -> int:
    """只补本机没填的设置键与本机没有的模型连接；返回补了几项。"""
    current = settings.portable_document()
    values = {k: v for k, v in (incoming.get("values") or {}).items()
              if k not in current["values"]}
    have = {p.get("id") for p in current["providers"]}
    providers = [p for p in incoming.get("providers") or []
                 if isinstance(p, dict) and p.get("id") and p.get("id") not in have]
    if values or providers:
        settings.merge_portable({"values": values, "providers": providers})
    return len(values) + len(providers)


# ── 读文件里的数据库 ────────────────────────────────────────


def _extract(archive: Path, member: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf, zf.open(member) as src, target.open("wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


def _read_snapshot(db_path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        collections = [dict(r) for r in db.execute(
            "SELECT coll_key, name, parent_key FROM collections WHERE origin = 'local'")]
        memberships: dict[str, list[str]] = {}
        for r in db.execute("SELECT doc_id, coll_key FROM document_collections"):
            memberships.setdefault(r["doc_id"], []).append(r["coll_key"])
        documents = []
        for r in db.execute(
            "SELECT doc_id, title, content_type, content_hash, tags, local_meta, attachment_key "
            "FROM documents WHERE origin = 'local' AND lifecycle_state = 'active' "
            "ORDER BY created_at"
        ):
            documents.append({**dict(r), "tags": json.loads(r["tags"] or "[]"),
                              "local_meta": json.loads(r["local_meta"] or "{}"),
                              "memberships": memberships.get(r["doc_id"], [])})
        zotero = int(db.execute(
            "SELECT COUNT(*) FROM documents WHERE origin != 'local'").fetchone()[0])
        filenames = {r["attachment_key"]: r["filename"] for r in db.execute(
            "SELECT attachment_key, filename FROM zotero_attachments WHERE library_id = 'LOCAL'")}
        notes: dict[str, list[str]] = {}
        for r in db.execute("SELECT doc_id, content FROM scoped_notes "
                            "WHERE scope_type = 'document' AND content != '' "
                            "ORDER BY created_at"):
            notes.setdefault(r["doc_id"], []).append(r["content"])
    return {"collections": collections, "documents": documents, "memberships": memberships,
            "zotero_documents": zotero, "filenames": filenames, "notes": notes}


__all__ = ["BIBLIO_KEYS", "LibraryWriter", "merge_archive"]
