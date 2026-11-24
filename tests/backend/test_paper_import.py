"""外部论文入库的契约测试（3.0.0 P4a）：文件名模板、找 PDF 的链接顺序、入库账本（内存 / SQLite）、
入库流程（下到 PDF / 待补 PDF / 补文件 / 删掉）、工具箱的下载接口。不联网。"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from kacore.adapters.tools.base import Tool, ToolEnv, ToolError
from kacore.adapters.tools.registry import ToolBox
from kacore.api import KnowledgeRepositoryApi
from kacore.config import PaperImportConfig, ToolsConfig
from kacore.domain.models import Collection, DocumentOrigin, SourceDocument
from kacore.domain.paper_import import (
    IMPORT_DISMISSED,
    IMPORT_IMPORTED,
    IMPORT_PENDING_PDF,
    ImportRecord,
    browser_links,
    clean_filename,
    paper_filename,
    pdf_candidates,
)
from kacore.domain.tool_calls import Block, Capture, Citation, ToolCall, ToolResult, ToolSpec
from kacore.managers.paper_importer import PaperImporter
from kacore.migration_runner import run_migrations
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore

PDF = b"%PDF-1.7\n%fake\n"
FIELDS = {"title": "Beyond Static Personas: Situational Personality Steering",
          "creators": ["Wei, Yu", "Chen, Lin", "Li, Mo"], "year": "2026",
          "identifier": "10.48550/arXiv.2601.01234"}


# ── 文件名 ──────────────────────────────────────────────────


def test_filename_follows_the_users_style() -> None:
    assert paper_filename(FIELDS) == \
        "Wei et al. (2026) - Beyond Static Personas, Situational Personality Steering.pdf"
    two = {**FIELDS, "creators": ["Mischel, Walter", "Shoda, Yuichi"]}
    assert paper_filename(two).startswith("Mischel and Shoda (2026) - ")
    one = {"title": "Art and Cosmotechnics", "creators": ["Hui, Yuk"], "year": "2021-03"}
    assert paper_filename(one) == "Hui (2021) - Art and Cosmotechnics.pdf"


def test_names_without_a_comma_stay_whole_and_missing_fields_have_defaults() -> None:
    assert paper_filename({"title": "T", "creators": ["Yuk Hui"]}) == "Yuk Hui (n.d.) - T.pdf"
    assert paper_filename({}) == "Anon. (n.d.) - Untitled.pdf"


def test_template_is_configurable_and_a_broken_template_falls_back() -> None:
    assert paper_filename(FIELDS, "{year} {authors}") == "2026 Wei et al..pdf"
    assert paper_filename(FIELDS, "{title") == paper_filename(FIELDS)
    assert paper_filename(FIELDS, "{unknown}{year}") == "2026.pdf"


def test_windows_characters_and_length_are_handled() -> None:
    name = clean_filename('A/B: "C"? <D>*|' + "x" * 300 + ".PDF")
    assert name.endswith(".pdf") and len(name) <= 154
    assert not set(name[:-4]) & set('/\\:"?<>*|')
    assert clean_filename("con.pdf") == "_con.pdf"


# ── 链接 ────────────────────────────────────────────────────


def _call(ord_: int, tool: str, text: str, arguments: dict | None = None,
          is_error: bool = False) -> ToolCall:
    return ToolCall(f"c_1.{ord_}", "c_1", ord_, tool, arguments or {}, [Block(text)], is_error)


def _citation(call_ord: int, url: str = "") -> Citation:
    fields = {**FIELDS, **({"url": url} if url else {})}
    return Citation("c_1", 1, f"c_1.{call_ord}", 0, fields, "passage")


def test_pdf_candidates_keep_a_fixed_order_and_skip_unrelated_blocks() -> None:
    calls = [
        _call(1, "search", f"Title: {FIELDS['title']}\nURL: https://doi.org/10.1/x\n"
                           "Open-access PDF: https://arxiv.org/pdf/2601.01234."),
        _call(2, "search", "Title: Something else\nURL: https://other.org/y"),
        _call(3, "read_open_pdf", "page text", {"url": "https://arxiv.org/pdf/2601.01234",
                                                "query": "q"}),
        _call(4, "search", f"Title: {FIELDS['title']} https://bad.org/z", is_error=True),
    ]
    urls = pdf_candidates(_citation(3, url="https://doi.org/10.1/x"), calls)
    assert urls == ["https://arxiv.org/pdf/2601.01234", "https://doi.org/10.1/x"]
    assert pdf_candidates(_citation(1), calls)[0] == "https://doi.org/10.1/x"


def test_browser_links_start_with_the_doi_link_then_tried_links() -> None:
    links = browser_links({**FIELDS, "url": "https://example.org/a"},
                          [{"url": "https://example.org/a", "error": "web page"},
                           {"url": "https://pub.org/b", "error": "403"}])
    assert links == ["https://doi.org/10.48550/arxiv.2601.01234", "https://example.org/a",
                     "https://pub.org/b"]


# ── 账本（接口对换）──────────────────────────────────────────


@pytest.fixture(params=["memory", "sqlite"])
async def store(request):
    if request.param == "memory":
        yield InMemorySourceDocumentStore()
        return
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)
    yield SQLiteSourceDocumentStore(conn)
    await conn.close()


async def test_import_records_round_trip_and_filter(store) -> None:
    a = ImportRecord("imp_a", "c_1", 1, FIELDS, "a.pdf", "L1", IMPORT_PENDING_PDF,
                     source={"question": "q"}, tried=[{"url": "u", "error": "e"}],
                     created_at="2026-11-23T10:00:00", updated_at="2026-11-23T10:00:00")
    b = ImportRecord("imp_b", "c_2", 2, {}, "b.pdf", "L1", IMPORT_IMPORTED, doc_id="d1",
                     created_at="2026-11-23T11:00:00", updated_at="2026-11-23T11:00:00")
    await store.save_import(a)
    await store.save_import(b)
    assert await store.get_import("imp_a") == a
    assert [r.import_id for r in await store.list_imports()] == ["imp_b", "imp_a"]
    assert [r.import_id for r in await store.list_imports(status=IMPORT_PENDING_PDF)] == ["imp_a"]
    assert [r.import_id for r in await store.list_imports(doc_id="d1")] == ["imp_b"]
    a.status = IMPORT_DISMISSED
    await store.save_import(a)
    assert (await store.get_import("imp_a")).status == IMPORT_DISMISSED


# ── 入库流程 ────────────────────────────────────────────────


class _Box:
    """假的工具箱：ok 里的链接返回 PDF，其余像网页一样被拒。"""

    def __init__(self, pdf: Path, ok: set[str]) -> None:
        self.pdf, self.ok, self.tried = pdf, ok, []

    async def fetch_file(self, url: str) -> tuple[Path, str]:
        self.tried.append(url)
        if url in self.ok:
            return self.pdf, "read_open_pdf"
        raise ToolError("the link does not return a PDF (probably a web page)")


class _Register:
    """假的上传管线：像 ingest 一样建一篇本地文档，local_meta 里是从文件名猜的作者。"""

    def __init__(self, store: InMemorySourceDocumentStore) -> None:
        self.store, self.seen = store, []

    async def __call__(self, *, title, file_path, content_type, size_bytes, content_hash,
                       collection):
        path = Path(file_path)
        assert path.name == title and path.read_bytes().startswith(b"%PDF-")
        self.seen.append((title, collection, size_bytes))
        doc_id = f"LOCAL_{len(self.seen)}"
        await self.store.add_document(SourceDocument(
            doc_id=doc_id, title=title, file_path=f"/library/{doc_id}/original.pdf",
            content_type=content_type, size_bytes=size_bytes, content_hash=content_hash,
            collection=collection, local_meta={"creators": ["Wei"], "chunk_schema": 3}))
        return doc_id


async def _setup(tmp_path: Path, ok: set[str]):
    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="Agentic", coll_key="L1"))
    await store.upsert_collection(Collection(name="Zot", coll_key="9:AB", read_only=True,
                                             origin=DocumentOrigin.ZOTERO, library_id="9"))
    await store.save_capture(Capture("c_1", "conv", 2, "persona?", "ui", "2026-11-23T10:00:00"))
    for call in (_call(1, "search", f"Title: {FIELDS['title']}\nURL: https://doi.org/10.1/x"),
                 _call(2, "read_open_pdf", "p5 text", {"url": "https://arxiv.org/pdf/1"})):
        await store.save_tool_call(call)
    await store.save_citation(Citation("c_1", 1, "c_1.2", 0, dict(FIELDS), "p5 text",
                                       "page 5", 5))
    pdf = tmp_path / "cache.pdf"
    pdf.write_bytes(PDF)
    box = _Box(pdf, ok)
    register = _Register(store)
    importer = PaperImporter(store=store, toolbox=box, register=register,  # type: ignore[arg-type]
                             staging_dir=tmp_path / "staging",
                             config_provider=PaperImportConfig)
    return store, box, register, importer


async def test_import_downloads_the_pdf_and_writes_biblio_and_provenance(tmp_path: Path) -> None:
    store, box, register, importer = await _setup(tmp_path, {"https://arxiv.org/pdf/1"})
    record = await importer.import_citation("c_1", 1, coll_key="L1")
    assert record.status == IMPORT_IMPORTED and record.pdf_url == "https://arxiv.org/pdf/1"
    assert box.tried == ["https://arxiv.org/pdf/1"]
    assert register.seen == [(record.filename, "Agentic", len(PDF))]
    assert record.filename.startswith("Wei et al. (2026) - Beyond Static Personas, ")
    doc = await store.get_document(record.doc_id)
    assert doc.title == FIELDS["title"] and doc.collection_keys == ["L1"]
    assert doc.local_meta["creators"] == FIELDS["creators"]
    assert doc.local_meta["doi"] == "10.48550/arxiv.2601.01234"
    assert doc.local_meta["chunk_schema"] == 3
    assert record.source["call_label"] == "c2" and record.source["page"] == 5
    assert not (tmp_path / "staging" / record.import_id).exists()
    again = await importer.import_citation("c_1", 1, coll_key="L1")
    assert again.import_id == record.import_id and len(register.seen) == 1


async def test_no_pdf_leaves_a_pending_record_with_links_then_a_pdf_completes_it(
        tmp_path: Path) -> None:
    store, box, register, importer = await _setup(tmp_path, set())
    record = await importer.import_citation("c_1", 1, coll_key="L1", filename="My name")
    assert record.status == IMPORT_PENDING_PDF and record.filename == "My name.pdf"
    assert [t["url"] for t in record.tried] == ["https://arxiv.org/pdf/1", "https://doi.org/10.1/x"]
    assert "web page" in record.tried[0]["error"] and register.seen == []
    assert browser_links(record.fields, record.tried)[0].startswith("https://doi.org/10.48550/")

    not_pdf = tmp_path / "page.html"
    not_pdf.write_text("<html>")
    with pytest.raises(ValueError, match="not a PDF"):
        await importer.attach_pdf(record.import_id, not_pdf)
    done = await importer.attach_pdf(record.import_id, tmp_path / "cache.pdf")
    assert done.status == IMPORT_IMPORTED and done.pdf_url == ""
    assert register.seen[0][0] == "My name.pdf"


async def test_dismiss_and_target_rules(tmp_path: Path) -> None:
    store, _box, _register, importer = await _setup(tmp_path, set())
    with pytest.raises(ValueError, match="local collection"):
        await importer.import_citation("c_1", 1, coll_key="9:AB")
    with pytest.raises(ValueError, match="not found"):
        await importer.import_citation("c_1", 9, coll_key="L1")
    record = await importer.import_citation("c_1", 1, coll_key="L1")
    assert (await importer.dismiss(record.import_id)).status == IMPORT_DISMISSED
    assert await store.list_imports(status=IMPORT_PENDING_PDF) == []


async def test_api_preview_lists_local_targets_and_flags_papers_already_in_the_library(
        tmp_path: Path) -> None:
    store, _box, _register, importer = await _setup(tmp_path, set())
    api = KnowledgeRepositoryApi(source_store=store)
    api.attach_paper_importer(importer)
    preview = await api.import_preview("c_1", 1)
    assert [t["coll_key"] for t in preview["targets"]] == ["L1"]
    assert preview["in_library"] is None and preview["candidates"] == 2
    await store.add_document(SourceDocument(
        doc_id="d9", title="Existing", file_path="/x.pdf", content_type="application/pdf",
        size_bytes=1, content_hash="h", collection="Agentic",
        local_meta={"doi": "10.48550/ARXIV.2601.01234"}))
    assert (await api.import_preview("c_1", 1))["in_library"] == {"doc_id": "d9",
                                                                  "title": "Existing"}
    pending = await api.import_external_source("c_1", 1, collection_key="L1")
    assert pending["target_name"] == "Agentic" and pending["links"]
    detail = await api.get_external_capture("c_1")
    assert detail["sources"][0]["import"]["status"] == IMPORT_PENDING_PDF


# ── 工具箱的下载接口 ────────────────────────────────────────


class _FileTool(Tool):
    spec = ToolSpec("file_tool", "Download.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block("x")])

    async def fetch_file(self, url: str) -> Path:
        if "bad" in url:
            raise ToolError("not a PDF")
        return Path("/cache/x.pdf")


class _PlainTool(Tool):
    spec = ToolSpec("plain_tool", "Search.", {"type": "object"})

    async def call(self, arguments):
        return ToolResult([Block("x")])


def _toolbox(cfg: ToolsConfig) -> ToolBox:
    return ToolBox(config_provider=lambda: cfg, read_secret=lambda _r: "",
                   env=ToolEnv(cache_dir=Path("/nonexistent")), tools=(_PlainTool, _FileTool))


async def test_toolbox_downloads_through_tools_that_implement_fetch_file() -> None:
    assert await _toolbox(ToolsConfig()).fetch_file("https://ok") == (Path("/cache/x.pdf"),
                                                                      "file_tool")
    with pytest.raises(ToolError, match="not a PDF"):
        await _toolbox(ToolsConfig()).fetch_file("https://bad")
    with pytest.raises(ToolError, match="no enabled tool"):
        await _toolbox(ToolsConfig(enabled={"file_tool": False})).fetch_file("https://ok")


# ── 路由 ────────────────────────────────────────────────────


async def test_upload_with_import_id_completes_the_pending_import(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiohttp import FormData
    from aiohttp.test_utils import TestClient, TestServer

    from web.server import build_app

    seen: list[tuple[str, bytes]] = []

    async def attach(import_id: str, path: Path) -> dict:
        seen.append((import_id, path.read_bytes()))
        return {"import_id": import_id, "status": IMPORT_IMPORTED}

    api = SimpleNamespace(attach_import_pdf=attach, register_document=AsyncMock(),
                          list_paper_imports=AsyncMock(return_value=[{"import_id": "imp_1"}]))
    client = TestClient(TestServer(build_app(api=api, upload_dir=tmp_path, token="t")),
                        headers={"Authorization": "Bearer t"})
    await client.start_server()
    try:
        form = FormData()
        form.add_field("file", PDF, filename="dl.pdf", content_type="application/pdf")
        form.add_field("import_id", "imp_1")
        resp = await client.post("/api/documents", data=form)
        assert resp.status == 200 and (await resp.json())["status"] == IMPORT_IMPORTED
        assert seen == [("imp_1", PDF)] and not api.register_document.await_count
        assert not [p for p in tmp_path.iterdir() if p.is_file()]
        resp = await client.get("/api/imports?status=pending_pdf")
        assert await resp.json() == [{"import_id": "imp_1"}]
    finally:
        await client.close()


# ── 「库里已有」：DOI 之外也认 ISBN（用户 11-24：没有 DOI 的书不该重复出现）─────────────


def test_isbn_keys_unify_10_and_13_digit_forms_and_drop_bad_check_digits() -> None:
    from kacore.domain.paper_import import identifier_keys, isbn_keys

    assert isbn_keys("978-0-8223-4776-7 0-8223-4776-8") == ["9780822347767"]
    assert isbn_keys("ISBN 0822347768") == ["9780822347767"]
    assert isbn_keys("080442957X") == ["9780804429573"]
    assert isbn_keys("9780822347760") == []            # 末位校验不过
    assert identifier_keys("https://doi.org/10.1/AB") == ["10.1/ab"]   # DOI 优先，只用 DOI
    assert identifier_keys("978-0-8223-4776-7") == ["isbn:9780822347767"]
    assert identifier_keys("arXiv:2401.01234") == [] and identifier_keys("") == []


async def test_books_in_zotero_or_imported_earlier_count_as_already_in_the_library() -> None:
    from kacore.domain.models import ZoteroItem

    store = InMemorySourceDocumentStore()

    async def doc(doc_id: str, **kw) -> None:
        await store.add_document(SourceDocument(
            doc_id=doc_id, title=doc_id, file_path=f"/{doc_id}.pdf", content_type="application/pdf",
            size_bytes=1, content_hash=doc_id, collection="c", **kw))

    # Zotero Web API 的原始字段是 ISBN；本地 zotero.sqlite 读出来的在 fields.ISBN 里。
    await doc("z_web", library_id="9", zotero_item_key="AAA")
    await store.upsert_zotero_item(ZoteroItem("AAA", "9", "book", raw_zotero_json={
        "ISBN": "978-0-8223-4776-7 0-8223-4776-8"}))
    await doc("z_local", library_id="9", zotero_item_key="BBB")
    await store.upsert_zotero_item(ZoteroItem("BBB", "9", "book", raw_zotero_json={
        "itemType": "book", "fields": {"ISBN": "0-262-01153-0"}}))
    # 入库来的书：local_meta 只有 DOI（这本没有），ISBN 只在入库记录里。
    await doc("imp", local_meta={"title": "Parables"})
    await store.save_import(ImportRecord("imp_1", "c_1", 1, {"identifier": "9780804429573"},
                                         "x.pdf", "L1", status=IMPORT_IMPORTED, doc_id="imp"))
    # 入库后又删掉的文档不算。
    await store.save_import(ImportRecord("imp_2", "c_1", 2, {"identifier": "9780199253807"},
                                         "y.pdf", "L1", status=IMPORT_IMPORTED, doc_id="gone"))

    api = KnowledgeRepositoryApi(source_store=store)
    assert await api._in_library("0822347768") == "z_web"
    assert await api._in_library("ISBN 978-0-262-01153-2") == "z_local"
    assert await api._in_library("080442957X") == "imp"
    assert await api._in_library("9780199253807") is None
    assert await api._in_library("arXiv:2401.01234") is None
