"""Zotero 推送的契约测试（3.0.0 P5a）：目标文件夹与 attachments: 路径、移交文件、按模板建独立附件、
写入失败时移回、前提检查、入库到 Zotero 集合时入库后立刻推送、Web API 写方法。不联网。"""
from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from kacore.adapters.zotero.web_api import ZoteroWebApiClient, ZoteroWebApiError
from kacore.config import ZoteroSyncConfig
from kacore.domain.models import (
    Collection,
    DocumentOrigin,
    SourceDocument,
    ZoteroAttachment,
)
from kacore.domain.paper_import import (
    IMPORT_IMPORTED,
    PUSH_FAILED,
    PUSH_PUSHED,
    ImportRecord,
)
from kacore.managers.zotero_pusher import PushError, ZoteroPusher
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

PDF = b"%PDF-1.7\n%fake\n"
TEMPLATE = {"itemType": "attachment", "linkMode": "linked_file", "title": "", "accessDate": "",
            "url": "", "note": "", "contentType": "", "charset": "", "path": "", "tags": [],
            "collections": [], "relations": {}}


class _Client:
    def __init__(self, *, write: bool = True, fail: str = "") -> None:
        self.write, self.fail, self.created = write, fail, []

    def get_current_key(self) -> dict[str, Any]:
        return {"userID": 123, "username": "u",
                "access": {"user": {"library": True, "write": self.write}}}

    def item_template(self, link_mode: str) -> dict[str, Any]:
        assert link_mode == "linked_file"
        return dict(TEMPLATE)

    def create_items(self, user_id: str, items: list, write_token: str) -> dict[str, Any]:
        if self.fail:
            raise ZoteroWebApiError(self.fail, status=403)
        self.created.append((user_id, items, write_token))
        return {"key": "NEWKEY01", "version": 7}


async def _setup(tmp_path: Path, client: _Client, **cfg: Any):
    store = InMemorySourceDocumentStore()
    library = tmp_path / "PhD - The Library"
    root = library / "Zotero"
    (root / "Other").mkdir(parents=True)
    (root / "Other" / "old.pdf").write_bytes(PDF)
    await store.upsert_zotero_attachment(ZoteroAttachment(
        attachment_key="OLD1", parent_item_key="", library_id="123", link_mode="linked_file",
        path="attachments:Zotero/Other/old.pdf", filename="old.pdf"))
    await store.upsert_collection(Collection(
        name="Lit: Review", coll_key="123:PARENT", origin=DocumentOrigin.ZOTERO, read_only=True,
        zotero_collection_key="PARENT", library_id="123"))
    await store.upsert_collection(Collection(
        name="Agentic", coll_key="123:COLLA", parent_key="123:PARENT",
        origin=DocumentOrigin.ZOTERO, read_only=True, zotero_collection_key="COLLA",
        library_id="123"))
    await store.upsert_collection(Collection(name="Mine", coll_key="L1"))
    original = tmp_path / "ka" / "library" / "LOCAL_I_A" / "original.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(PDF)
    await store.add_document(SourceDocument(
        doc_id="LOCAL_I_A", title="Beyond Static Personas", file_path=str(original),
        content_type="application/pdf", size_bytes=len(PDF), content_hash="h", collection="Mine",
        zotero_item_key="I", attachment_key="A", local_meta={"doi": "10.1/X"}))
    await store.save_import(ImportRecord("imp_1", "c_1", 1, {}, "Wei et al. (2026) - T.pdf",
                                         "L1", IMPORT_IMPORTED, doc_id="LOCAL_I_A"))
    config = ZoteroSyncConfig(storage_mode="zotmoov", zotmoov_root=str(root))
    config = replace(config, **cfg)
    pusher = ZoteroPusher(store=store, config_provider=lambda: config,
                          api_key_provider=lambda: "key", client_factory=lambda _k: client)
    return store, pusher, library, original


async def test_push_moves_the_file_into_the_collection_path_and_creates_the_attachment(
        tmp_path: Path) -> None:
    client = _Client()
    store, pusher, library, original = await _setup(tmp_path, client)
    record = await pusher.push("LOCAL_I_A", "123:COLLA")

    dest = library / "Zotero" / "Lit, Review" / "Agentic" / "Wei et al. (2026) - T.pdf"
    assert record.status == PUSH_PUSHED and record.attachment_key == "NEWKEY01"
    assert record.path == "attachments:Zotero/Lit, Review/Agentic/Wei et al. (2026) - T.pdf"
    assert dest.read_bytes() == PDF and not original.exists()
    assert (await store.get_document("LOCAL_I_A")).file_path == str(dest)
    [(user_id, [item], token)] = client.created
    assert user_id == "123" and token == record.write_token
    assert item == {**TEMPLATE, "title": dest.name, "path": record.path,
                    "contentType": "application/pdf", "collections": ["COLLA"],
                    "url": "https://doi.org/10.1/x"}
    assert (await store.push_claims()) == {"NEWKEY01": "LOCAL_I_A"}
    with pytest.raises(PushError, match="already pushed"):
        await pusher.push("LOCAL_I_A", "123:COLLA")


async def test_existing_files_of_the_collection_decide_the_folder(tmp_path: Path) -> None:
    store, pusher, library, _ = await _setup(tmp_path, _Client())
    await store.upsert_zotero_attachment(ZoteroAttachment(
        attachment_key="IN1", parent_item_key="", library_id="123", link_mode="linked_file",
        path="attachments:Zotero/Custom Folder/x.pdf", filename="x.pdf"))
    await store.set_item_collections("123", "IN1", ["COLLA"])
    plan = await pusher.plan("LOCAL_I_A", "123:COLLA")
    assert plan.folder == library / "Zotero" / "Custom Folder"
    assert plan.path == "attachments:Zotero/Custom Folder/Wei et al. (2026) - T.pdf"
    plan.folder.mkdir(parents=True)
    (plan.folder / "Wei et al. (2026) - T.pdf").write_bytes(b"other")
    again = await pusher.plan("LOCAL_I_A", "123:COLLA")
    assert again.dest.name == "Wei et al. (2026) - T (2).pdf"


async def test_a_failed_write_moves_the_file_back(tmp_path: Path) -> None:
    store, pusher, library, original = await _setup(tmp_path, _Client(fail="Forbidden"))
    record = await pusher.push("LOCAL_I_A", "123:COLLA")
    assert record.status == PUSH_FAILED and "Forbidden" in record.message
    assert original.read_bytes() == PDF
    assert not (library / "Zotero" / "Lit, Review" / "Agentic" / "Wei et al. (2026) - T.pdf"
                ).exists()
    assert (await store.get_document("LOCAL_I_A")).file_path == str(original)
    assert await store.push_claims() == {}


async def test_preconditions_are_checked_before_anything_moves(tmp_path: Path) -> None:
    store, pusher, _library, original = await _setup(tmp_path, _Client(write=False))
    with pytest.raises(PushError, match="no write access"):
        await pusher.push("LOCAL_I_A", "123:COLLA")
    assert original.exists()
    with pytest.raises(PushError, match="not a Zotero collection"):
        await pusher.push("LOCAL_I_A", "L1")
    _store, other, _l, _o = await _setup(tmp_path / "b", _Client(), storage_mode="managed_copy")
    with pytest.raises(PushError, match="ZotMoov storage mode"):
        await other.plan("LOCAL_I_A", "123:COLLA")


async def test_base_folder_setting_wins_and_must_contain_the_zotmoov_folder(
        tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _store, pusher, _l, _o = await _setup(tmp_path, _Client(), base_dir=str(elsewhere))
    with pytest.raises(PushError, match="not inside the Zotero base folder"):
        await pusher.plan("LOCAL_I_A", "123:COLLA")


async def test_import_into_a_zotero_collection_pushes_right_after(tmp_path: Path) -> None:
    from kacore.config import PaperImportConfig
    from kacore.domain.tool_calls import Block, Capture, Citation, ToolCall
    from kacore.managers.paper_importer import PaperImporter

    client = _Client()
    store, pusher, library, _ = await _setup(tmp_path, client)
    await store.save_capture(Capture("c_9", "conv", 1, "q", "ui", "t"))
    await store.save_tool_call(ToolCall("c_9.1", "c_9", 1, "read_open_pdf",
                                        {"url": "https://x/p.pdf"}, [Block("text")]))
    await store.save_citation(Citation("c_9", 1, "c_9.1", 0, {"title": "Drift",
                                       "creators": ["Gao, R"], "year": "2025"}, "text"))
    pdf = tmp_path / "cache.pdf"
    pdf.write_bytes(PDF)

    class Box:
        async def fetch_file(self, url: str):
            return pdf, "read_open_pdf"

    async def register(*, title, file_path, content_type, size_bytes, content_hash, collection):
        dest = tmp_path / "ka" / "library" / "LOCAL_N_B" / "original.pdf"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(Path(file_path).read_bytes())
        await store.add_document(SourceDocument(
            doc_id="LOCAL_N_B", title=title, file_path=str(dest), content_type=content_type,
            size_bytes=size_bytes, content_hash=content_hash, collection=collection))
        return "LOCAL_N_B"

    importer = PaperImporter(store=store, toolbox=Box(), register=register,  # type: ignore[arg-type]
                             staging_dir=tmp_path / "staging", config_provider=PaperImportConfig,
                             pusher=pusher)
    record = await importer.import_citation("c_9", 1, coll_key="123:COLLA")
    assert record.status == IMPORT_IMPORTED
    push = await store.get_push("LOCAL_N_B")
    assert push is not None and push.status == PUSH_PUSHED
    assert (library / "Zotero" / "Lit, Review" / "Agentic" / "Gao (2025) - Drift.pdf").exists()


# ── Web API 写方法 ──────────────────────────────────────────


class _Resp(io.BytesIO):
    headers: dict[str, str] = {}


def test_create_items_sends_json_with_the_write_token_and_reads_the_key() -> None:
    seen: list[Any] = []

    def opener(request, timeout=None):
        seen.append(request)
        return _Resp(json.dumps({"successful": {"0": {"key": "K1", "version": 9}},
                                 "success": {"0": "K1"}, "failed": {}}).encode())

    client = ZoteroWebApiClient("key", opener=opener)
    assert client.create_items("123", [{"itemType": "attachment"}], "tok") == {"key": "K1",
                                                                             "version": 9}
    [req] = seen
    assert req.get_method() == "POST" and req.full_url.endswith("/users/123/items")
    assert req.get_header("Zotero-write-token") == "tok"
    assert json.loads(req.data) == [{"itemType": "attachment"}]

    def failing(request, timeout=None):
        return _Resp(json.dumps({"successful": {}, "failed": {
            "0": {"code": 400, "message": "'path' is not valid for linked_url"}}}).encode())

    with pytest.raises(ZoteroWebApiError, match="not valid"):
        ZoteroWebApiClient("key", opener=failing).create_items("123", [{}], "t")
