"""`.kaarch` 导出导入的契约测试（3.0.0 P6）：口令加密、导出内容、导入前读清单、完全覆盖走 R2 恢复、
合并只加本机没有的东西。不联网。"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from kacore.config import Config
from kacore.domain.models import Collection, DocumentOrigin, ScopedNote, SourceDocument
from kacore.managers.kaarch import KaarchManager
from kacore.managers.r2_restore import apply_pending_restore
from kacore.migration_runner import run_migrations
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore
from kacore.settings.memory import MemorySecretStore
from kacore.settings.store import SettingsStore
from kacore.utils.passphrase_box import WrongPassphrase, open_box, seal

CONFIG = Config({"vector_db": {"db_filename": "vector_store.db"},
                 "graph": {"working_dir": "lightrag_workspaces"}})
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def test_passphrase_box_round_trip_and_wrong_passphrase() -> None:
    box = seal({"zotero.server_api_key": "k1"}, "correct horse", n=1 << 10)
    assert open_box(box, "correct horse") == {"zotero.server_api_key": "k1"}
    with pytest.raises(WrongPassphrase):
        open_box(box, "wrong")
    with pytest.raises(ValueError):
        seal({"a": "b"}, "")


async def _source(tmp_path: Path):
    """导出方：真实 SQLite 库（迁移全套）、两个本地集合、两篇本地文档（一篇带笔记）、
    一篇 Zotero 文档。"""
    data = tmp_path / "a"
    (data / "library").mkdir(parents=True)
    db_path = data / "knowledge_repository.db"
    conn = await aiosqlite.connect(db_path)
    await run_migrations(conn)
    store = SQLiteSourceDocumentStore(conn)
    await store.upsert_collection(Collection(name="Papers", coll_key="LP", created_at=NOW))
    await store.upsert_collection(Collection(name="Sub", coll_key="LS", parent_key="LP",
                                             created_at=NOW))
    for doc_id, body, coll, origin in (("d_same", b"%PDF-same", "LP", "local"),
                                       ("d_new", b"%PDF-new", "LS", "local"),
                                       ("d_zot", b"%PDF-zot", "LP", "zotero")):
        pdf = data / "library" / doc_id / "original.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(body)
        await store.add_document(SourceDocument(
            doc_id=doc_id, title=f"Title {doc_id}", file_path=str(pdf),
            content_type="application/pdf", size_bytes=len(body), content_hash=f"h_{doc_id}",
            collection="Papers", tags=["t1"], created_at=NOW, updated_at=NOW,
            origin=DocumentOrigin(origin), collection_keys=[coll],
            local_meta={"creators": ["Gao, R"], "year": "2025", "chunk_schema": 3}))
    await store.add_scoped_note(ScopedNote(id="n1", scope_type="document", scope_key="d_new",
                                           content="My note", doc_id="d_new", created_at=NOW,
                                           updated_at=NOW))
    secrets = MemorySecretStore({"zotero.server_api_key": "zkey", "notion.token": "ntok"})
    settings = SettingsStore.in_memory({"flow.external.max_rounds": 6,
                                        "flow.external.import_filename": "{title}"},
                                       secrets=secrets)
    manager = KaarchManager(data_dir=data, db_path=db_path, source_store=store, config=CONFIG,
                            settings=settings, secrets=secrets, embedding_fingerprint="fp1")
    return manager, conn


async def _run(manager: KaarchManager, start: dict[str, Any]) -> dict[str, Any]:
    assert start["status"] == "running"
    await manager._task  # type: ignore[misc]
    job = manager.job()
    assert job is not None and job["status"] == "success", job
    return job["result"]


async def _export(tmp_path: Path, parts: list[str]) -> Path:
    manager, conn = await _source(tmp_path)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    try:
        result = await _run(manager, manager.start_export(str(out), parts, "pw", "3.0.0"))
    finally:
        await conn.close()
    return Path(result["path"])


async def test_export_holds_manifest_settings_encrypted_keys_and_the_library(
        tmp_path: Path) -> None:
    archive = await _export(tmp_path, ["settings", "secrets", "library"])
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        box = json.loads(zf.read("secrets.json"))
        settings = json.loads(zf.read("settings.json"))
    assert {"payload/database/knowledge_repository.db",
            "payload/library/d_new/original.pdf"} <= names
    assert not any(n.startswith("payload/config/") for n in names)
    assert manifest["parts"] == ["settings", "secrets", "library"]
    assert manifest["counts"]["local_documents"] == 2 and manifest["counts"]["notes"] == 1
    assert manifest["secret_count"] == 2 and manifest["app_version"] == "3.0.0"
    assert "zkey" not in archive.read_bytes().decode("latin-1")
    assert open_box(box, "pw") == {"notion.token": "ntok", "zotero.server_api_key": "zkey"}
    assert settings["values"]["flow.external.max_rounds"] == 6

    manager, conn = await _source(tmp_path / "x")
    try:
        with pytest.raises(ValueError, match="passphrase"):
            manager.start_export(str(tmp_path / "out"), ["secrets"], "")
    finally:
        await conn.close()


async def test_inspect_then_overwrite_restores_the_library_on_restart(tmp_path: Path) -> None:
    archive = await _export(tmp_path, ["settings", "secrets", "library"])
    target = tmp_path / "b"
    target.mkdir()
    with closing(sqlite3.connect(target / "knowledge_repository.db")) as db:
        db.execute("CREATE TABLE documents (doc_id TEXT PRIMARY KEY, file_path TEXT)")
        db.commit()
    store = InMemorySourceDocumentStore()
    await store.add_document(SourceDocument(
        doc_id="old", title="Old", file_path="/x.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h", collection="default"))
    secrets = MemorySecretStore({"zotero.server_api_key": "local"})
    reloads: list[int] = []

    async def reload() -> None:
        reloads.append(1)

    manager = KaarchManager(data_dir=target, db_path=target / "knowledge_repository.db",
                            source_store=store, config=CONFIG, settings=None, secrets=secrets,
                            embedding_fingerprint="fp1", reload_callback=reload)
    info = await manager.inspect(str(archive))
    assert info["local_empty"] is False and info["embedding_same"] is True
    assert info["counts"]["documents"] == 3 and info["secret_count"] == 2
    with pytest.raises(WrongPassphrase):
        manager.start_import(str(archive), "overwrite", "bad")

    result = await _run(manager, manager.start_import(str(archive), "overwrite", "pw"))
    assert result["restart"] is True and result["secrets_written"] == 2
    assert secrets.get_secret("zotero.server_api_key") == "zkey"
    assert apply_pending_restore(target, CONFIG) is True
    with closing(sqlite3.connect(target / "knowledge_repository.db")) as db:
        ids = {r[0] for r in db.execute("SELECT doc_id FROM documents")}
    assert ids == {"d_same", "d_new", "d_zot"}
    restored = json.loads((target / "settings.json").read_text())
    assert restored["values"]["flow.external.max_rounds"] == 6


class _Writer:
    """合并用的 api 方法：建集合、入库（像 ingest 一样建本地文档）、建笔记。"""

    def __init__(self, store: InMemorySourceDocumentStore) -> None:
        self.store, self.notes, self.registered = store, [], []

    async def create_subcollection(self, name: str, parent_key: str = "",
                                   description: str = "") -> str:
        key = f"L_{name}"
        await self.store.upsert_collection(Collection(name=name, coll_key=key,
                                                      parent_key=parent_key))
        return key

    async def register_document(self, *, title, file_path, content_type, size_bytes,
                                content_hash, collection, tags=None) -> str:
        assert Path(file_path).read_bytes().startswith(b"%PDF-")
        doc_id = f"LOCAL_{len(self.registered)}"
        self.registered.append((title, collection))
        await self.store.add_document(SourceDocument(
            doc_id=doc_id, title=title, file_path=file_path, content_type=content_type,
            size_bytes=size_bytes, content_hash=content_hash, collection=collection,
            tags=list(tags or [])))
        return doc_id

    async def create_document_note(self, doc_id: str, content: str):
        self.notes.append((doc_id, content))
        return {"id": "n"}


async def test_merge_adds_only_what_is_missing(tmp_path: Path) -> None:
    archive = await _export(tmp_path, ["settings", "secrets", "library"])
    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="Papers", coll_key="L_mine"))
    await store.add_document(SourceDocument(
        doc_id="mine", title="Same", file_path="/same.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h_d_same", collection="Papers"))
    secrets = MemorySecretStore({"zotero.server_api_key": "local"})
    settings = SettingsStore.in_memory({"flow.external.max_rounds": 2}, secrets=secrets)
    writer = _Writer(store)
    manager = KaarchManager(data_dir=tmp_path / "b", db_path=tmp_path / "b" / "k.db",
                            source_store=store, config=CONFIG, settings=settings,
                            secrets=secrets, writer=writer)

    result = await _run(manager, manager.start_import(str(archive), "merge", "pw"))

    assert (result["documents_added"], result["documents_present"],
            result["zotero_skipped"]) == (1, 1, 1)
    assert result["collections_added"] == 1 and result["notes_added"] == 1
    sub = await store.get_collection("L_Sub")
    assert sub is not None and sub.parent_key == "L_mine"
    added = await store.get_document("LOCAL_0")
    assert added.title == "Title d_new" and added.collection_keys == ["L_Sub"]
    assert added.local_meta == {"creators": ["Gao, R"], "year": "2025"}
    assert writer.notes == [("LOCAL_0", "My note")]
    assert settings.get("flow.external.max_rounds") == 2
    assert settings.get("flow.external.import_filename") == "{title}"
    assert secrets.get_secret("zotero.server_api_key") == "local"
    assert secrets.get_secret("notion.token") == "ntok"
    assert result["secrets_added"] == 1 and result["settings_added"] == 1
