"""web/server.py 路由 smoke 测试（aiohttp TestServer + 内存 api）。

验证 HTTP↔kacore/api 的翻译与认证中间件，不连真实后端。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import fitz  # type: ignore[import-untyped]
import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from settings_helpers import legacy_settings
from static_retriever import StaticRetriever

from kacore.api import KnowledgeRepositoryApi
from kacore.app_initializer import AppInitializer
from kacore.config import Config
from kacore.domain.models import (
    Collection,
    DocumentChunk,
    DocumentOrigin,
    SourceDocument,
    SyncTargetKind,
)
from kacore.index_compatibility import IndexCompatibilityStore
from kacore.log_capture import MemoryLogHandler
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.sync_targets.memory import InMemorySyncTarget
from kacore.secret_store import EncryptedSecretStore
from web.server import build_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

_GB = 1024 * 1024 * 1024


async def _make_api() -> KnowledgeRepositoryApi:
    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="papers", description="d"))
    await store.add_document(
        SourceDocument("d1", "Doc 1", "/p/d1.pdf", "application/pdf", 100, "h1", "papers", ["t"])
    )
    kb = StaticRetriever(
        {
            "papers": [
                DocumentChunk(
                    "c0",
                    "d1",
                    0,
                    "alpha beta",
                    "h0",
                    metadata={"page_number": 1, "locator": "page_1_p1", "paragraph": 1},
                )
            ]
        }
    )
    targets = {
        SyncTargetKind.R2: InMemorySyncTarget(SyncTargetKind.R2, 10 * _GB, base_used_bytes=8 * _GB),
        SyncTargetKind.NOTION: InMemorySyncTarget(SyncTargetKind.NOTION, 0),
    }
    return KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=kb,
        sync_targets=targets,
        config=Config({"notion_sync": {"database_id": "db1", "parent_page_id": "parent1"}}),
    )


async def _client(tmp_path: Path, *, authenticated: bool = True) -> TestClient:
    api = await _make_api()
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH if authenticated else None)
    await client.start_server()
    return client


async def test_cleaning_maintenance_requires_auth_and_defaults_to_preview(tmp_path):
    from types import SimpleNamespace

    api = SimpleNamespace(
        reprocess_documents_with_stale_cleaning=AsyncMock(return_value={"dry_run": True}),
        get_document_footnotes=AsyncMock(return_value=[]),
    )
    client = TestClient(TestServer(build_app(api=api, upload_dir=tmp_path, token=TOKEN)))
    await client.start_server()
    try:
        response = await client.post("/api/documents/reprocess-cleaning", json={})
        assert response.status == 401
        client.session.headers.update(AUTH)
        response = await client.post("/api/documents/reprocess-cleaning", json={})
        assert response.status == 200
        api.reprocess_documents_with_stale_cleaning.assert_awaited_once_with(dry_run=True)
        response = await client.get("/api/documents/doc/footnotes")
        assert response.status == 200 and await response.json() == []
        response = await client.post("/api/documents/reprocess-cleaning", json={"dry_run": "false"})
        assert response.status == 400
    finally:
        await client.close()


# ── 配置校验 ────────────────────────────────────────────────────


def test_build_app_rejects_empty_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="token"):
        build_app(api=object(), upload_dir=tmp_path, token="")  # type: ignore[arg-type]


def test_build_app_requires_exactly_one_api_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        build_app(upload_dir=tmp_path, token=TOKEN)
    with pytest.raises(ValueError, match="exactly one"):
        build_app(
            api=object(),  # type: ignore[arg-type]
            api_provider=lambda: None,
            upload_dir=tmp_path,
            token=TOKEN,
        )


# ── 集合 / 文档（no-auth）──────────────────────────────────────


async def test_list_and_create_collection(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/collections")
        assert resp.status == 200
        assert [c["name"] for c in await resp.json()] == ["papers"]

        resp = await client.post("/api/collections", json={"name": "manuals", "description": "m"})
        assert resp.status == 200
        created = await resp.json()
        assert created["name"] == "manuals"
        assert created["description"] == "m"
        assert created["coll_key"].startswith("L")
        assert created["parent_key"] == ""
        names = [c["name"] for c in await (await client.get("/api/collections")).json()]
        assert set(names) == {"papers", "manuals"}
    finally:
        await client.close()


async def test_subcollection_tree_crud(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        root = await (await client.post("/api/collections", json={"name": "root"})).json()
        child = await (
            await client.post(
                "/api/collections", json={"name": "child", "parent_key": root["coll_key"]}
            )
        ).json()
        assert child["parent_key"] == root["coll_key"]

        # 重命名
        r = await client.patch(
            f"/api/collections/by-key/{child['coll_key']}", json={"name": "child2"}
        )
        assert r.status == 200
        cols = {c["coll_key"]: c for c in await (await client.get("/api/collections")).json()}
        assert cols[child["coll_key"]]["name"] == "child2"

        # 防环：把 root 移到其后代 child 下应失败
        r = await client.patch(
            f"/api/collections/by-key/{root['coll_key']}",
            json={"parent_key": child["coll_key"]},
        )
        assert r.status == 400

        # 删除 root：child 提升为顶层
        r = await client.delete(f"/api/collections/by-key/{root['coll_key']}")
        assert r.status == 200
        cols = {c["coll_key"]: c for c in await (await client.get("/api/collections")).json()}
        assert root["coll_key"] not in cols
        assert cols[child["coll_key"]]["parent_key"] == ""
    finally:
        await client.close()

async def test_create_collection_requires_name(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/collections", json={"name": "  "})
        assert resp.status == 400
    finally:
        await client.close()


async def test_list_documents_and_filter(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/documents")
        docs = await resp.json()
        assert [d["doc_id"] for d in docs] == ["d1"]
        assert docs[0]["size"] == docs[0]["size_bytes"] == 100
        assert docs[0]["filename"] == "d1.pdf"
        assert docs[0]["ext"] == "pdf"
        assert docs[0]["chunks"] == 0
        resp = await client.get("/api/documents?collection=none")
        assert await resp.json() == []
    finally:
        await client.close()


async def test_upload_classify_delete_document(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        form = FormData()
        form.add_field("file", b"%PDF-1.4 demo", filename="new.pdf", content_type="application/pdf")
        form.add_field("collection", "papers")
        form.add_field("tags", "x, y")
        resp = await client.post("/api/documents", data=form)
        assert resp.status == 200
        uploaded_resource = await resp.json()
        doc_id = uploaded_resource["doc_id"]
        assert uploaded_resource["title"] == "new.pdf"
        assert uploaded_resource["size"] == len(b"%PDF-1.4 demo")

        # 上传后列表 +1，且标签生效
        docs = await (await client.get("/api/documents")).json()
        uploaded = next(d for d in docs if d["doc_id"] == doc_id)
        assert uploaded["tags"] == ["x", "y"]
        assert uploaded["size_bytes"] == len(b"%PDF-1.4 demo")

        # 分类
        resp = await client.patch(f"/api/documents/{doc_id}", json={"collection": "manuals"})
        assert resp.status == 200
        assert (await resp.json())["collection"] == "manuals"
        moved = next(
            d for d in await (await client.get("/api/documents")).json() if d["doc_id"] == doc_id
        )
        assert moved["collection"] == "manuals"

        # 删除
        assert (await client.delete(f"/api/documents/{doc_id}")).status == 200
        assert (await client.delete(f"/api/documents/{doc_id}")).status == 404
    finally:
        await client.close()


async def test_upload_empty_file_rejected(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        form = FormData()
        form.add_field("file", b"", filename="empty.pdf")
        resp = await client.post("/api/documents", data=form)
        assert resp.status == 400
    finally:
        await client.close()


async def test_upload_moves_staging_file_into_managed_pdf_repository(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    initializer = AppInitializer(legacy_settings({"vector_db": {"backend": "lexical"}}), data_dir)
    await initializer.initialize()
    assert initializer.api is not None

    app = build_app(
        api=initializer.api,
        upload_dir=data_dir / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        pdf_path = tmp_path / "managed.pdf"
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 750), "managed repository upload")
        pdf.save(pdf_path)
        pdf.close()

        form = FormData()
        form.add_field(
            "file",
            pdf_path.read_bytes(),
            filename=pdf_path.name,
            content_type="application/pdf",
        )
        response = await client.post("/api/documents", data=form)
        assert response.status == 200
        doc_id = (await response.json())["doc_id"]

        stored = await initializer.api.get_document(doc_id)
        assert stored is not None
        # 制品包模型：原件落在 library/<document_id>/original.pdf
        assert Path(stored.file_path) == data_dir / "library" / doc_id / "original.pdf"
        assert Path(stored.file_path).exists()
        assert (data_dir / "library" / doc_id / "clean.md").exists()
        assert list((data_dir / "uploads").iterdir()) == []
    finally:
        await client.close()
        await initializer.teardown()


# ── KB 检索 / 配额 ─────────────────────────────────────────────


async def test_kb_search(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        assert await (await client.get("/api/kb/collections")).json() == ["papers"]
        hits = await (await client.get("/api/kb/search?collection=papers&q=alpha&top_k=5")).json()
        assert [h["chunk_id"] for h in hits] == ["c0"]
    finally:
        await client.close()


async def test_effective_config_endpoint_returns_masked_config(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/config/effective")
        assert resp.status == 200
        body = await resp.json()
        assert body["notion_sync"]["database_id"] == "db1"
        assert "r2_sync" in body
        assert "graph" in body
    finally:
        await client.close()


# ── 认证 ────────────────────────────────────────────────────────


async def test_api_requires_bearer_token(tmp_path: Path) -> None:
    client = await _client(tmp_path, authenticated=False)
    try:
        assert (await client.get("/api/collections")).status == 401
        wrong = await client.get("/api/collections", headers={"Authorization": "Bearer nope"})
        assert wrong.status == 401
        ok = await client.get("/api/collections", headers=AUTH)
        assert ok.status == 200
        # 浏览器导航（下载）无法带请求头：GET 可用 access_token 查询参数，写操作不行。
        assert (await client.get(f"/api/collections?access_token={TOKEN}")).status == 200
        posted = await client.post(f"/api/collections?access_token={TOKEN}", json={"name": "x"})
        assert posted.status == 401
    finally:
        await client.close()


async def test_removed_login_and_static_routes_are_gone(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        for method, path in (("get", "/api/auth"), ("post", "/api/login"), ("post", "/api/logout")):
            assert (await getattr(client, method)(path)).status in {404, 405}
        assert (await client.get("/")).status == 404
    finally:
        await client.close()


async def test_cors_allows_only_desktop_shell_origins(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        for origin in ("tauri://localhost", "http://tauri.localhost"):
            preflight = await client.options(
                "/api/collections",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )
            assert preflight.status == 204
            assert preflight.headers["Access-Control-Allow-Origin"] == origin
            assert "Authorization" in preflight.headers["Access-Control-Allow-Headers"]
            resp = await client.get("/api/collections", headers={"Origin": origin})
            assert resp.status == 200
            assert resp.headers["Access-Control-Allow-Origin"] == origin
        denied = await client.get("/api/collections", headers={"Origin": "http://evil.example"})
        assert denied.status == 403
        assert "Access-Control-Allow-Origin" not in denied.headers
        # 401 也要带 CORS 头，否则前端拿不到状态码只能看到网络错误。
        unauth = await client.get(
            "/api/collections",
            headers={"Origin": "tauri://localhost", "Authorization": "Bearer nope"},
        )
        assert unauth.status == 401
        assert unauth.headers["Access-Control-Allow-Origin"] == "tauri://localhost"
    finally:
        await client.close()


async def test_dev_origins_can_be_added(tmp_path: Path) -> None:
    app = build_app(
        api=await _make_api(),
        upload_dir=tmp_path,
        token=TOKEN,
        allowed_origins=("http://localhost:3000",),
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        ok = await client.get("/api/collections", headers={"Origin": "http://localhost:3000"})
        assert ok.status == 200
        tauri = await client.get("/api/collections", headers={"Origin": "tauri://localhost"})
        assert tauri.status == 403
    finally:
        await client.close()


async def test_health_and_shutdown_routes(tmp_path: Path) -> None:
    calls: list[str] = []
    app = build_app(
        api=await _make_api(),
        upload_dir=tmp_path,
        token=TOKEN,
        shutdown_callback=lambda: calls.append("stop"),
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        assert await (await client.get("/api/health")).json() == {"status": "ok"}
        resp = await client.post("/api/shutdown")
        assert resp.status == 202 and calls == ["stop"]
    finally:
        await client.close()

    client = await _client(tmp_path)
    try:
        assert (await client.post("/api/shutdown")).status == 501
    finally:
        await client.close()


async def test_api_provider_returns_503_while_service_restarts(tmp_path: Path) -> None:
    current: dict[str, Any] = {"api": None}
    app = build_app(api_provider=lambda: current["api"], upload_dir=tmp_path, token=TOKEN)
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        assert (await client.get("/api/collections")).status == 503
        current["api"] = await _make_api()
        assert (await client.get("/api/collections")).status == 200
    finally:
        await client.close()


# ── 预留端口（reserved → 501 + available_in）────────────────────


@pytest.mark.parametrize(
    "method,path,version",
    [
        ("post", "/api/sync/r2", "v0.3.0 / v0.4.0"),
        ("post", "/api/sync/all", "v0.3.0 / v0.4.0"),
        ("post", "/api/backup", "v0.29.0"),
        ("post", "/api/restore", "v0.29.0"),
        # /api/graph/build 与 /api/graph 已在 v0.15.1 移除 _reserved() 包裹，不再测 501
    ],
)
async def test_reserved_endpoints_return_501(
    tmp_path: Path, method: str, path: str, version: str
) -> None:
    client = await _client(tmp_path)
    try:
        kwargs = {"json": {}} if method == "post" else {}
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status == 501
        body = await resp.json()
        assert body["status"] == "reserved"
        assert body["reserved"] is True
        assert body["available_in"] == version
    finally:
        await client.close()


async def test_sync_status_endpoint_returns_200(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/sync/status")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
    finally:
        await client.close()


async def test_sync_status_routes_show_notion_ledger_failures(tmp_path: Path) -> None:
    from kacore.domain.models import NotionEntityRecord

    api = await _make_api()
    await api._source_store.upsert_notion_entity(NotionEntityRecord(
        "document", "d1", status="failed", message="Notion token 无效",
    ))
    client = TestClient(TestServer(build_app(
        api=api, upload_dir=tmp_path,
        token=TOKEN,
    )), headers=AUTH)
    await client.start_server()
    try:
        records = await (await client.get("/api/sync/status")).json()
        assert records[0]["status"] == "failed"
        summary = await (await client.get("/api/sync/notion/status")).json()
        assert summary["documents"]["failed"] == 1
        assert summary["errors"] == [{"message": "Notion token 无效", "count": 1}]
    finally:
        await client.close()


async def test_notion_initialization_route_preserves_preflight_error(tmp_path: Path) -> None:
    api = await _make_api()
    api.initialize_notion_database = AsyncMock(return_value={
        "status": "error", "error_code": "unauthorized", "message": "请重新填写 Notion token",
    })
    client = TestClient(TestServer(build_app(
        api=api, upload_dir=tmp_path,
        token=TOKEN,
    )), headers=AUTH)
    await client.start_server()
    try:
        body = await (await client.post("/api/notion/init", json={})).json()
        assert body["error_code"] == "unauthorized"
        assert body["message"] == "请重新填写 Notion token"
    finally:
        await client.close()


async def test_zotero_probe_endpoint_returns_connection_and_read(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/zotero/probe")
        assert resp.status == 200
        body = await resp.json()
        assert "connection" in body
        assert "read" in body
        # 未装配 zotero pipeline → read 不可用但接口仍稳定返回
        assert body["read"]["available"] is False
    finally:
        await client.close()


async def test_graph_endpoints_return_200(tmp_path: Path) -> None:
    class StubLightRAGRegistry:
        def has_workspace(self, collection: str) -> bool:
            return collection == "papers"

        async def insert_document(
            self,
            collection: str,
            doc_id: str,
            text: str,
            *,
            lrag_chunks: list[str] | None = None,
            progress_callback=None,
        ) -> None:
            self.inserted = (collection, doc_id, text)

        async def query(self, collection: str, query: str) -> dict:
            return {
                "answer": "LightRAG answer",
                "context": "LightRAG context",
                "collection": collection,
                "engine": "lightrag_core",
                "debug": {"query_mode": "mix"},
            }

        async def probe_llm_ready(self) -> None:
            # build_graph() 启动前的就绪度探针（v1.0.11）；本测试的图谱构建 LLM 本身
            # 不是被测对象，直接放行即可。
            return None

    class StubEmbeddingProvider:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1]

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] for _ in texts]

        def get_dimension(self) -> int:
            return 1

    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="papers", description="d"))
    await store.add_document(
        SourceDocument("d1", "Doc 1", "/p/d1.pdf", "application/pdf", 100, "h1", "papers")
    )
    await store.replace_chunks("d1", [DocumentChunk("c1", "d1", 0, "source text", "ch1")])
    await store.set_lightrag_index_status("d1", "papers", "indexed")
    compatibility = IndexCompatibilityStore(tmp_path / "compat.json")
    compatibility.mark_lightrag_compatible("papers", "fp")
    api = KnowledgeRepositoryApi(
        source_store=store,
        lightrag_registry=StubLightRAGRegistry(),  # type: ignore[arg-type]
        index_compatibility=compatibility,
        embedding_fingerprint="fp",
        embedding_provider=StubEmbeddingProvider(),  # type: ignore[arg-type]
    )

    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )

    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        estimate = await client.post("/api/graph/build/estimate", json={"collection": "papers"})
        assert estimate.status == 200
        estimate_body = await estimate.json()
        assert "LightRAG chunks" in estimate_body["estimate_notice"]
        assert "estimated_lrag_chunks" in estimate_body

        resp1 = await client.post("/api/graph/build", json={"collection": "papers"})
        assert resp1.status == 400
        resp1 = await client.post(
            "/api/graph/build", json={"collection": "papers", "confirmed": True}
        )
        assert resp1.status == 200
        body1 = await resp1.json()
        assert body1["engine"] == "lightrag_core"
        assert body1["collection"] == "papers"
        assert body1["job_id"]

        job_resp = await client.get(f"/api/graph/build/{body1['job_id']}")
        assert job_resp.status == 200
        assert (await job_resp.json())["engine"] == "lightrag_core"
    finally:
        await client.close()


async def test_graph_build_cancel_endpoint_returns_200(tmp_path: Path) -> None:
    """POST /api/graph/build/{job_id}/cancel 取消一个活跃任务并返回清理摘要。"""
    from kacore.lightrag_core import BuildJob

    api = await _make_api()
    job = BuildJob(job_id="job-1", collection="papers", status="running")
    api._graph_build_jobs["job-1"] = job

    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post("/api/graph/build/job-1/cancel", json={"cleanup": True})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "cancelled"
        assert body["job_id"] == "job-1"
        assert "cleanup" in body

        missing = await client.post("/api/graph/build/does-not-exist/cancel", json={})
        assert missing.status == 404
    finally:
        await client.close()


async def test_notion_init_and_push_note_routes_return_200(tmp_path: Path) -> None:
    class StubApi:
        async def initialize_notion_database(
            self,
            parent_page_id: str | None = None,
            database_title: str | None = None,
        ) -> dict:
            return {
                "status": "success",
                "database_id": "db-created",
                "qa_database_id": "qa-created",
                "parent_page_id": parent_page_id,
                "database_title": database_title,
                "created": True,
            }

        async def push_note_to_notion(
            self,
            content: str,
            *,
            title: str = "",
            tags: list[str] | None = None,
            citations: list[str] | None = None,
            source: str = "ask",
            keep_local: bool = False,
        ) -> dict:
            return {
                "status": "success",
                "page_id": "qa-page-1",
                "echo_title": title,
                "echo_citations": citations or [],
            }

    app = build_app(
        api=StubApi(),  # type: ignore[arg-type]
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        init = await client.post(
            "/api/notion/init",
            json={"parent_page_id": "parent", "database_title": "KR"},
        )
        assert init.status == 200
        assert (await init.json())["qa_database_id"] == "qa-created"

        push = await client.post(
            "/api/notion/push-note",
            json={"content": "研究结论", "title": "Q1", "citations": ["d1", "d2"]},
        )
        assert push.status == 200
        body = await push.json()
        assert body["page_id"] == "qa-page-1"
        assert body["echo_title"] == "Q1"
        assert body["echo_citations"] == ["d1", "d2"]
    finally:
        await client.close()


async def test_notion_routes_report_unavailable_instead_of_reserved(tmp_path: Path) -> None:
    class StubApi:
        async def initialize_notion_database(self, *_args, **_kwargs) -> dict:
            return {"status": "unavailable", "message": "Notion 同步管线未装配"}

        async def push_note_to_notion(self, *_args, **_kwargs) -> dict:
            return {"status": "unavailable", "message": "Notion 同步管线未装配"}

    app = build_app(
        api=StubApi(),  # type: ignore[arg-type]
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        init = await client.post("/api/notion/init", json={})
        assert init.status == 503
        init_body = await init.json()
        assert init_body["status"] == "unavailable"
        assert "reserved" not in init_body

        push = await client.post("/api/notion/push-note", json={"content": "x"})
        assert push.status == 503
        push_body = await push.json()
        assert push_body["status"] == "unavailable"
        assert "reserved" not in push_body
    finally:
        await client.close()


async def test_agent_evidence_route_never_returns_plugin_answer(tmp_path: Path) -> None:
    api = await _make_api()
    retrieve = AsyncMock(
        return_value={
            "question": "compare",
            "actual_retrieval_mode": "agent_enhanced",
            "evidence": [{"doc_id": "d1", "text": "evidence"}],
            "requires_agent_assessment": True,
            "plugin_llm_used": False,
            "full_text_used": False,
        }
    )
    api.retrieve_agent_evidence = retrieve  # type: ignore[method-assign]
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/ask/evidence",
            json={
                "question": "compare",
                "queries": ["alpha", "beta"],
                "collection": "papers",
                "retrieval_mode": "enhanced",
                "round": 1,
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["plugin_llm_used"] is False
        assert body["full_text_used"] is False
        assert body["requires_agent_assessment"] is True
        assert retrieve.await_args.kwargs["queries"] == ["alpha", "beta"]

        invalid = await client.post(
            "/api/ask/evidence",
            json={"question": "compare", "queries": "not-a-list"},
        )
        assert invalid.status == 400
    finally:
        await client.close()


async def test_document_page_route_returns_only_requested_page(tmp_path: Path) -> None:
    api = await _make_api()
    page = AsyncMock(
        return_value={
            "doc_id": "d1",
            "start": 10,
            "end": 20,
            "total_chars": 100,
            "has_more": True,
            "content": "0123456789",
        }
    )
    api.get_document_markdown_page = page  # type: ignore[method-assign]
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/documents/d1/content/page?start=10&max_chars=10")
        assert resp.status == 200
        assert (await resp.json())["content"] == "0123456789"
        page.assert_awaited_once_with("d1", start=10, max_chars=10, confirmed=False)

        # v1.1.1：max_chars 不再被静默夹取到 40000，0 表示读到文末，confirmed 可透传。
        page.reset_mock()
        await client.get("/api/documents/d1/content/page?max_chars=60001")
        page.assert_awaited_once_with("d1", start=0, max_chars=60001, confirmed=False)

        page.reset_mock()
        await client.get("/api/documents/d1/content/page?max_chars=0&confirmed=1")
        page.assert_awaited_once_with("d1", start=0, max_chars=0, confirmed=True)
    finally:
        await client.close()


async def test_document_page_route_returns_409_when_confirmation_required(
    tmp_path: Path,
) -> None:
    """超阈值单次读取返回 409 询问体，且**不含任何正文**。"""
    from kacore.api import FullTextConfirmationRequiredError

    api = await _make_api()
    api.get_document_markdown_page = AsyncMock(  # type: ignore[method-assign]
        side_effect=FullTextConfirmationRequiredError(
            "d1",
            title="Long Paper",
            total_chars=183421,
            requested_chars=183421,
            threshold_chars=60000,
            estimated_tokens=172530,
        )
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/documents/d1/content/page?max_chars=0")
        assert resp.status == 409
        body = await resp.json()
        assert body["status"] == "fulltext_confirmation_required"
        assert body["total_chars"] == 183421
        assert body["threshold_chars"] == 60000
        assert body["estimated_tokens"] == 172530
        assert "content" not in body
    finally:
        await client.close()


async def test_codex_graph_routes_translate_task_protocol(tmp_path: Path) -> None:
    api = await _make_api()
    start = AsyncMock(
        return_value={
            "job_id": "job-1",
            "status": "waiting_agent",
            "plugin_llm_used": False,
        }
    )
    next_task = AsyncMock(
        return_value={
            "job": {"job_id": "job-1"},
            "task": {"task_id": "task-1", "content": "bounded"},
            "complete": False,
        }
    )
    submit = AsyncMock(return_value={"accepted": True})
    retry = AsyncMock(return_value={"task_id": "task-1", "status": "pending"})
    api.build_graph_with_codex = start  # type: ignore[method-assign]
    api.get_next_codex_graph_task = next_task  # type: ignore[method-assign]
    api.submit_codex_graph_task = submit  # type: ignore[method-assign]
    api.retry_codex_graph_task = retry  # type: ignore[method-assign]
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        started = await client.post(
            "/api/graph/codex-build",
            json={"collection": "papers", "confirmed": True},
        )
        assert started.status == 200
        assert (await started.json())["job_id"] == "job-1"
        start.assert_awaited_once_with("papers", confirmed=True)

        fetched = await client.get("/api/graph/codex-build/job-1/next")
        assert fetched.status == 200
        assert (await fetched.json())["task"]["task_id"] == "task-1"

        rejected = await client.post(
            "/api/graph/codex-build/job-1/submit",
            json={"task_id": "task-1"},
        )
        assert rejected.status == 400
        accepted = await client.post(
            "/api/graph/codex-build/job-1/submit",
            json={
                "task_id": "task-1",
                "result": {"entities": [], "relationships": []},
            },
        )
        assert accepted.status == 200
        submit.assert_awaited_once_with(
            "job-1", "task-1", {"entities": [], "relationships": []}
        )

        retried = await client.post(
            "/api/graph/codex-build/job-1/retry",
            json={"task_id": "task-1"},
        )
        assert retried.status == 200
        retry.assert_awaited_once_with("job-1", "task-1")
    finally:
        await client.close()


# ── /api/ask ──────────────────────────────────────────────────────


async def test_ask_route_returns_answer_and_sources(tmp_path: Path) -> None:
    """POST /api/ask 应委派 api.ask() 并返回 answer + sources JSON。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post(
            "/api/ask",
            json={"question": "alpha beta", "collection": "papers", "top_k": 5},
        )
        assert resp.status == 200
        body = await resp.json()
        assert "answer" in body and body["answer"]
        assert "sources" in body and isinstance(body["sources"], list)
        assert len(body["sources"]) > 0
        src = body["sources"][0]
        assert "chunk_id" in src and src["chunk_id"] == "c0"
        assert "doc_id" in src and src["doc_id"] == "d1"
        assert "metadata" in src and src["metadata"]
        assert "locator" in src["metadata"] and src["metadata"]["locator"] == "page_1_p1"
        assert "conversation_id" in body and body["conversation_id"]
    finally:
        await client.close()


async def test_ask_route_forwards_doc_id_and_confirmed(tmp_path: Path) -> None:
    """doc_id / confirmed 必须透传给 api.ask()。

    回归锁：前端从 v1.0.x 起就一直在 body 里发 doc_id，但 handle_ask 从未读取过——
    全文检索因此始终跑不通。这条断言防止它再次被静默丢弃。
    """
    api = await _make_api()
    ask = AsyncMock(return_value={"conversation_id": "c1", "answer": "ok", "sources": []})
    api.ask = ask  # type: ignore[method-assign]
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/ask",
            json={
                "question": "q",
                "retrieval_mode": "fulltext",
                "doc_id": "d1",
                "confirmed": True,
            },
        )
        assert resp.status == 200
        assert ask.await_args.kwargs["doc_id"] == "d1"
        assert ask.await_args.kwargs["confirmed"] is True

        # 缺省时 doc_id 为 None、confirmed 为 False（确认类 flag 必须默认关）。
        ask.reset_mock()
        await client.post("/api/ask", json={"question": "q"})
        assert ask.await_args.kwargs["doc_id"] is None
        assert ask.await_args.kwargs["confirmed"] is False
    finally:
        await client.close()


async def test_ask_route_maps_fulltext_errors_to_status_codes(tmp_path: Path) -> None:
    """全文检索三类领域异常各自映射到 409 / 404 / 502，且都带 status 判别串。"""
    from kacore.api import (
        FullTextConfirmationRequiredError,
        FullTextDocumentUnavailableError,
        FullTextGenerationFailedError,
    )

    api = await _make_api()
    ask = AsyncMock()
    api.ask = ask  # type: ignore[method-assign]
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    payload = {"question": "q", "retrieval_mode": "fulltext", "doc_id": "d1"}
    try:
        ask.side_effect = FullTextConfirmationRequiredError(
            "d1",
            title="Long Paper",
            total_chars=183421,
            requested_chars=183421,
            threshold_chars=60000,
            estimated_tokens=172530,
        )
        resp = await client.post("/api/ask", json=payload)
        assert resp.status == 409
        body = await resp.json()
        assert body["status"] == "fulltext_confirmation_required"
        assert body["title"] == "Long Paper"
        assert body["total_chars"] == 183421
        assert body["threshold_chars"] == 60000

        ask.side_effect = FullTextDocumentUnavailableError("d1", "artifact_missing")
        resp = await client.post("/api/ask", json=payload)
        assert resp.status == 404
        body = await resp.json()
        assert body["status"] == "fulltext_document_unavailable"
        assert body["reason"] == "artifact_missing"

        ask.side_effect = FullTextGenerationFailedError("d1", 183421, "context overflow")
        resp = await client.post("/api/ask", json=payload)
        assert resp.status == 502
        body = await resp.json()
        assert body["status"] == "fulltext_generation_failed"
        assert body["total_chars"] == 183421
    finally:
        await client.close()


async def test_ask_route_requires_question(tmp_path: Path) -> None:
    """question 为空时应返回 400。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/ask", json={"question": "  "})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body
    finally:
        await client.close()


async def test_ask_route_accepts_conversation_id(tmp_path: Path) -> None:
    """conversation_id 由调用方提供时应原样返回。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post(
            "/api/ask",
            json={"question": "alpha", "conversation_id": "test-conv-123"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["conversation_id"] == "test-conv-123"
    finally:
        await client.close()


async def test_ask_route_clamps_top_k(tmp_path: Path) -> None:
    """top_k 超出边界应被截断而不是报错。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/ask", json={"question": "alpha", "top_k": 999})
        assert resp.status == 200
    finally:
        await client.close()


async def test_download_document_route(tmp_path: Path) -> None:
    """文档下载端点应正确下载物理原件，不存在的 doc_id 或物理文件应返回 404。"""
    api = await _make_api()
    
    # 模拟真实物理原件写入
    file_path = tmp_path / "test_doc.pdf"
    file_path.write_bytes(b"pdf data here")
    
    # 在内存 store 中更新文档物理路径
    doc = await api._source_store.get_document("d1")
    assert doc is not None
    doc.file_path = str(file_path)
    await api._source_store.update_document(doc)

    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        # 1. 下载存在的文档
        resp = await client.get("/api/documents/d1/raw")
        assert resp.status == 200
        assert (await resp.read()) == b"pdf data here"
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Doc 1"'

        # 2. 下载不存在的文档
        resp_missing = await client.get("/api/documents/missing-id/raw")
        assert resp_missing.status == 404
        
        # 3. 物理文件缺失的文档
        doc.file_path = "/missing/path.pdf"
        await api._source_store.update_document(doc)
        resp_file_missing = await client.get("/api/documents/d1/raw")
        assert resp_file_missing.status == 404
    finally:
        await client.close()


async def test_document_reader_routes(tmp_path: Path) -> None:
    api = await _make_api()
    bundle = tmp_path / "library" / "d1"
    bundle.mkdir(parents=True)
    raw_pdf = bundle / "original.pdf"
    raw_pdf.write_bytes(b"%PDF-1.4 reader")
    (bundle / "clean.md").write_text("# Clean\n\nMarkdown body", encoding="utf-8")

    doc = await api._source_store.get_document("d1")
    assert doc is not None
    doc.file_path = str(raw_pdf)
    doc.markdown_rel_path = "clean.md"
    await api._source_store.update_document(doc)
    await api._source_store.replace_chunks(
        "d1",
        [
            DocumentChunk(
                "c1",
                "d1",
                0,
                "source text",
                "h1",
                metadata={"page_number": 3, "locator": "page_3_p1"},
            )
        ],
    )

    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        content = await client.get("/api/documents/d1/content?format=md")
        assert content.status == 200
        assert await content.text() == "# Clean\n\nMarkdown body"
        assert content.headers["Content-Type"].startswith("text/markdown")

        chunks = await client.get("/api/documents/d1/chunks")
        assert chunks.status == 200
        assert await chunks.json() == [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "ordinal": 0,
                "text": "source text",
                "page": 3,
                "metadata": {"page_number": 3, "locator": "page_3_p1"},
            }
        ]

        inline = await client.get("/api/documents/d1/raw?disposition=inline")
        assert inline.status == 200
        assert inline.headers["Content-Disposition"] == 'inline; filename="Doc 1"'

        default = await client.get("/api/documents/d1/raw")
        assert default.status == 200
        assert default.headers["Content-Disposition"] == 'attachment; filename="Doc 1"'
    finally:
        await client.close()


async def test_document_annotations_route_uses_zotero_readonly_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = await _make_api()
    doc = await api._source_store.get_document("d1")
    assert doc is not None
    doc.origin = DocumentOrigin.ZOTERO
    doc.library_id = "1"
    doc.zotero_item_key = "ITEM"
    doc.attachment_key = "ATTACH"
    await api._source_store.update_document(doc)

    class FakeZoteroClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def list_items(self, **kwargs: object) -> list[dict]:
            assert kwargs == {"item_type": "annotation", "include": "data"}
            return [
                {
                    "key": "ANN1",
                    "data": {
                        "key": "ANN1",
                        "itemType": "annotation",
                        "parentItem": "ATTACH",
                        "annotationType": "highlight",
                        "annotationText": "matched text",
                        "annotationComment": "comment",
                        "annotationColor": "#ffd400",
                        "annotationPageLabel": "7",
                        "annotationPosition": '{"pageIndex":6,"rects":[[1,2,3,4]]}',
                        "dateAdded": "2026-06-10T11:49:59Z",
                        "dateModified": "2026-06-10T11:50:00Z",
                    },
                },
                {
                    "key": "ANN2",
                    "data": {
                        "key": "ANN2",
                        "itemType": "annotation",
                        "parentItem": "OTHER",
                        "annotationText": "ignored",
                    },
                },
            ]

    from kacore.adapters.zotero import local_api

    monkeypatch.setattr(local_api, "ZoteroLocalApiClient", FakeZoteroClient)

    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/documents/d1/annotations")
        assert resp.status == 200
        assert await resp.json() == [
            {
                "id": "ANN1",
                "doc_id": "d1",
                "text": "matched text",
                "type": "highlight",
                "comment": "comment",
                "color": "#ffd400",
                "page": 7,
                "page_label": "7",
                "position": {"pageIndex": 6, "rects": [[1, 2, 3, 4]]},
                "created_at": "2026-06-10T11:49:59Z",
                "updated_at": "2026-06-10T11:50:00Z",
            }
        ]
    finally:
        await client.close()


async def test_document_and_collection_notes_routes(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/documents/d1/notes")
        assert resp.status == 200
        assert await resp.json() == []

        created = await client.post("/api/documents/d1/notes", json={"content": "note body"})
        assert created.status == 201
        note = await created.json()
        assert note["scope_type"] == "document"
        assert note["doc_id"] == "d1"
        assert note["raw_zotero_json"]["itemType"] == "note"

        collection_note = await client.post(
            "/api/collections/papers/notes",
            json={"content": "collection note"},
        )
        assert collection_note.status == 201
        body = await collection_note.json()
        assert body["scope_type"] == "collection"
        assert body["collection_name"] == "papers"

        listed = await client.get("/api/collections/papers/notes")
        assert listed.status == 200
        assert [n["id"] for n in await listed.json()] == [body["id"]]
    finally:
        await client.close()


async def test_chat_lock_route_and_preserve_locked_clear(tmp_path: Path) -> None:
    api = await _make_api()
    await api._source_store.add_chat_message("conv", "user", "q")
    await api._source_store.add_chat_message("conv", "assistant", "a")
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        locked = await client.patch(
            "/api/chat/history/conv/messages/1/lock",
            json={"locked": True},
        )
        assert locked.status == 200
        assert (await locked.json())["locked"] is True

        clear = await client.delete("/api/chat/history?conversation_id=conv&preserve_locked=true")
        assert clear.status == 200
        history = await client.get("/api/chat/history?conversation_id=conv")
        assert history.status == 200
        messages = (await history.json())["messages"]
        assert [(m["role"], m["locked"]) for m in messages] == [("assistant", True)]
    finally:
        await client.close()


async def test_console_scope_state_routes(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        missing = await client.get("/api/console/scope-state?scope_type=global&scope_key=console")
        assert missing.status == 404

        saved = await client.put(
            "/api/console/scope-state",
            json={
                "scope_type": "collection",
                "scope_key": "papers",
                "selected_collection": "papers",
                "selected_doc_id": "d1",
                "note_doc_id": "d1",
                "payload": {"right": "notes"},
            },
        )
        assert saved.status == 200
        assert (await saved.json())["selected_doc_id"] == "d1"

        loaded = await client.get("/api/console/scope-state?scope_type=collection&scope_key=papers")
        assert loaded.status == 200
        body = await loaded.json()
        assert body["note_doc_id"] == "d1"
        assert body["payload"] == {"right": "notes"}
    finally:
        await client.close()


async def test_config_update_route(tmp_path: Path) -> None:
    """测试 POST /api/config/update 接口更新配置以及写白名单保护。"""
    api = await _make_api()
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        # 1. 成功更新允许的配置键
        resp = await client.post(
            "/api/config/update",
            json={"section": "embedding", "key": "provider", "value": "external"}
        )
        assert resp.status == 200
        assert await resp.json() == {
            "status": "success",
            "restart_required": True,
            "rebuild_required": True,
            "message": "Configuration saved. Restart and rebuild indexes.",
        }

        resp_source = await client.post(
            "/api/config/update",
            json={"section": "source_store", "key": "default_collection", "value": "papers"},
        )
        assert resp_source.status == 200
        assert (await resp_source.json())["restart_required"] is True

        resp_deep = await client.post(
            "/api/config/update",
            json={"section": "deep_thinking", "key": "verify_enabled", "value": False},
        )
        assert resp_deep.status == 200
        deep_data = await resp_deep.json()
        assert deep_data["restart_required"] is False
        assert deep_data["rebuild_required"] is False

        resp_notion_mode = await client.post(
            "/api/config/update",
            json={"section": "notion_sync", "key": "sync_mode", "value": "strict"},
        )
        assert resp_notion_mode.status == 200
        notion_mode_data = await resp_notion_mode.json()
        assert notion_mode_data["restart_required"] is False
        assert notion_mode_data["rebuild_required"] is False

        resp_invalid_notion_mode = await client.post(
            "/api/config/update",
            json={"section": "notion_sync", "key": "sync_mode", "value": "mirror"},
        )
        assert resp_invalid_notion_mode.status == 400
        assert "notion_sync.sync_mode" in (
            await resp_invalid_notion_mode.json()
        )["message"]

        # 2. 已移除的旧 embedding 来源被拒绝；切回 local（上面已改成 external）要求重建索引。
        resp_astr_provider = await client.post(
            "/api/config/update",
            json={"section": "embedding", "key": "provider", "value": "astr"},
        )
        assert resp_astr_provider.status == 400
        resp_local = await client.post(
            "/api/config/update",
            json={"section": "embedding", "key": "provider", "value": "local"},
        )
        assert resp_local.status == 200
        assert (await resp_local.json())["rebuild_required"] is True

        resp_invalid_provider = await client.post(
            "/api/config/update",
            json={"section": "embedding", "key": "provider", "value": "bogus"},
        )
        assert resp_invalid_provider.status == 400
        assert "must be 'local' or 'external'" in (
            await resp_invalid_provider.json()
        )["message"]

        # 3a. 机密键即便其所在节（r2_sync）已开放 enabled 开关，也拒绝写入（命中键白名单）。
        resp_secret = await client.post(
            "/api/config/update",
            json={"section": "r2_sync", "key": "secret_access_key", "value": "hack"}
        )
        assert resp_secret.status == 400
        secret_data = await resp_secret.json()
        assert secret_data["status"] == "error"
        assert "is a secret" in secret_data["message"]

        resp_deep_secret = await client.post(
            "/api/config/update",
            json={"section": "deep_thinking", "key": "llm_api_key", "value": "hack"},
        )
        assert resp_deep_secret.status == 400
        deep_secret_data = await resp_deep_secret.json()
        assert deep_secret_data["status"] == "error"
        assert "not allowed" in deep_secret_data["message"]

        # 3b. 完全只读的配置节仍整节拒绝（命中节白名单）。
        resp_blocked = await client.post(
            "/api/config/update",
            json={"section": "web_console", "key": "password", "value": "hack"}
        )
        assert resp_blocked.status == 400
        data = await resp_blocked.json()
        assert data["status"] == "error"
        assert "write-protected" in data["message"]
    finally:
        await client.close()


async def test_capabilities_route_returns_pipeline_and_dependencies(tmp_path: Path) -> None:
    """GET /api/capabilities 返回环节快照 + 依赖状态 + 诊断。"""
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/capabilities")
        assert resp.status == 200
        data = await resp.json()
        stage_ids = [s["id"] for s in data["pipeline"]]
        assert stage_ids == [
            "zotero", "ingest", "embedding", "vector_store", "retrieval", "graph", "ask",
            "models", "providers", "notion", "r2",
        ]
        assert {d["key"] for d in data["dependencies"]} == {
            "local_embedding",
            "milvus",
            "lightrag",
            "r2",
        }
        assert isinstance(data["diagnostics"], list)
    finally:
        await client.close()


async def test_zotero_routes_respond(tmp_path: Path) -> None:
    """Zotero 路由：未装配管线时 config 返回结构、pull 返回错误、status 为空。"""
    client = await _client(tmp_path)
    try:
        cfg = await (await client.get("/api/zotero/config")).json()
        assert cfg["enabled"] is False
        assert cfg["sync_mode"] == "conservative"
        assert cfg["storage_mode"] == "managed_copy"
        assert cfg["zotmoov_root"] == ""

        pull = await (await client.post("/api/sync/zotero/pull", json={})).json()
        assert pull["status"] == "error"  # 未启用/未装配

        status = await (await client.get("/api/sync/zotero/status")).json()
        assert status == {}
    finally:
        await client.close()


async def test_zotero_server_key_routes_encrypt_and_mask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST/DELETE /api/zotero/server-key 保存密钥但不回显明文。"""

    class Client:
        def __init__(self, api_key: str) -> None:
            assert api_key == "zotero-secret"

        def get_current_key(self) -> dict[str, object]:
            return {
                "userID": 123,
                "username": "alice",
                "access": {"user": {"library": True, "files": True}},
            }

    monkeypatch.setattr("kacore.adapters.zotero.web_api.ZoteroWebApiClient", Client)
    api = await _make_api()
    api._secret_store = EncryptedSecretStore(tmp_path / "secrets")
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        saved = await client.post("/api/zotero/server-key", json={"api_key": "zotero-secret"})
        assert saved.status == 200
        body = await saved.json()
        assert body["server_key_present"] is True
        assert body["server_key_masked"] == "zo****et"
        assert "zotero-secret" not in str(body)

        removed = await client.delete("/api/zotero/server-key")
        assert removed.status == 200
        body2 = await removed.json()
        assert body2["server_key_present"] is False
        assert body2["server_key_masked"] == ""
    finally:
        await client.close()


async def test_zotero_account_change_requires_confirmation_and_preserves_old_key_on_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = {
        "old-key": {"userID": 123, "username": "alice"},
        "new-key": {"userID": 456, "username": "bob"},
    }

    class Client:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def get_current_key(self) -> dict[str, object]:
            return {
                **identities[self.api_key],
                "access": {"user": {"library": True, "files": True}},
            }

    monkeypatch.setattr("kacore.adapters.zotero.web_api.ZoteroWebApiClient", Client)
    api = await _make_api()
    api._secret_store = EncryptedSecretStore(tmp_path / "secrets")
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        first = await client.post("/api/zotero/server-key", json={"api_key": "old-key"})
        assert first.status == 200

        changed = await client.post("/api/zotero/server-key", json={"api_key": "new-key"})
        required = await changed.json()
        assert required["status"] == "account_change_required"
        assert required["current_account"]["account_id"] == "123"
        assert required["new_account"]["account_id"] == "456"
        assert api._secret_store.get_secret("zotero.server_api_key") == "old-key"

        cancelled = await client.post(
            "/api/zotero/account-change",
            json={"change_id": required["change_id"], "action": "cancel"},
        )
        assert (await cancelled.json())["status"] == "cancelled"
        assert api._secret_store.get_secret("zotero.server_api_key") == "old-key"

        changed_again = await client.post(
            "/api/zotero/server-key", json={"api_key": "new-key"}
        )
        required_again = await changed_again.json()
        api._preflight_zotero_snapshot = AsyncMock()  # type: ignore[method-assign]
        api._reset_local_zotero_mirror = AsyncMock()  # type: ignore[method-assign]
        api.sync_zotero_pull = AsyncMock(  # type: ignore[method-assign]
            return_value={"status": "started"}
        )
        replaced = await client.post(
            "/api/zotero/account-change",
            json={
                "change_id": required_again["change_id"],
                "action": "replace_local",
            },
        )
        result = await replaced.json()
        assert result["status"] == "replaced"
        assert result["sync"]["status"] == "started"
        assert api._secret_store.get_secret("zotero.server_api_key") == "new-key"
        assert await api._source_store.get_source_account_binding("zotero_server") == {
            "account_id": "456",
            "account_name": "bob",
        }
        api._preflight_zotero_snapshot.assert_awaited_once()
        api._reset_local_zotero_mirror.assert_awaited_once()
        api.sync_zotero_pull.assert_awaited_once_with(
            incremental=False, _skip_account_guard=True
        )
    finally:
        await client.close()


async def test_r2_backup_restore_status_and_job_routes(tmp_path: Path) -> None:
    class Manager:
        active_job = {"job_id": "j1", "status": "running", "stage": "upload"}

        def start_backup(self, *, force: bool = False) -> dict[str, object]:
            return {"status": "started", "force": force}

        def start_restore(self, *, auto_restart: bool = False) -> dict[str, object]:
            return {"status": "started", "auto_restart": auto_restart}

        async def storage_status(self) -> dict[str, object]:
            return {
                "status": "ready",
                "bucket_physical_bytes": 100,
                "plugin_physical_bytes": 40,
                "snapshot_logical_bytes": 55,
            }

    base = await _make_api()
    api = KnowledgeRepositoryApi(
        source_store=base._source_store,
        retrieval_orchestrator=base._retrieval_orchestrator,
        r2_backup_manager=Manager(),  # type: ignore[arg-type]
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        backup = await client.post("/api/backup", json={"force": True})
        assert await backup.json() == {"status": "started", "force": True}

        restore = await client.post("/api/restore", json={"auto_restart": False})
        assert await restore.json() == {"status": "started", "auto_restart": False}

        status = await client.get("/api/r2/status")
        assert (await status.json())["bucket_physical_bytes"] == 100

        job = await client.get("/api/r2/job")
        assert (await job.json())["job"]["stage"] == "upload"
    finally:
        await client.close()


async def test_dependencies_route_lists_optional_packages(tmp_path: Path) -> None:
    """GET /api/dependencies 列出可选依赖与安装状态。"""
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/dependencies")
        assert resp.status == 200
        deps = (await resp.json())["dependencies"]
        milvus = next(d for d in deps if d["key"] == "milvus")
        assert milvus["pip_spec"] == "pymilvus[milvus_lite]>=2.6,<3.0"
        assert "installed" in milvus
    finally:
        await client.close()


async def test_dependency_install_rejects_unlisted_package(tmp_path: Path) -> None:
    """POST /api/dependencies/install 拒绝白名单外包名（防注入），不触发 pip。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post(
            "/api/dependencies/install", json={"package": "evil; rm -rf /"}
        )
        assert resp.status == 400
        assert (await resp.json())["status"] == "error"

        missing = await client.post("/api/dependencies/install", json={})
        assert missing.status == 400
    finally:
        await client.close()


async def test_dependency_recheck_returns_fresh_state(tmp_path: Path) -> None:
    """POST /api/dependencies/recheck 重新探测并返回最新状态。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/dependencies/recheck")
        assert resp.status == 200
        data = await resp.json()
        assert "dependencies" in data
    finally:
        await client.close()


async def test_ask_route_sources_contain_locator_fields(tmp_path: Path) -> None:
    """POST /api/ask 返回的 sources 每条应包含 chunk_id/doc_id/metadata 字段。"""
    store = InMemorySourceDocumentStore()
    await store.upsert_collection(Collection(name="papers", description="d"))
    await store.add_document(
        SourceDocument("d1", "Doc 1", "/p/d1.pdf", "application/pdf", 100, "h1", "papers", [])
    )
    locator_meta = {"page_number": 3, "locator": "page_3_p2", "paragraph": 2}
    kb = StaticRetriever(
        {"papers": [DocumentChunk("c0", "d1", 0, "alpha beta", "h0", metadata=locator_meta)]}
    )
    api = KnowledgeRepositoryApi(
        source_store=store,
        retrieval_orchestrator=kb,
        sync_targets={},
        config=Config({}),
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/ask",
            json={"question": "alpha beta", "collection": "papers"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["sources"], "sources 不应为空"
        src = body["sources"][0]
        assert "chunk_id" in src, "sources[0] 缺少 chunk_id"
        assert "doc_id" in src, "sources[0] 缺少 doc_id"
        assert "metadata" in src, "sources[0] 缺少 metadata"
        assert src["chunk_id"] == "c0"
        assert src["doc_id"] == "d1"
        assert src["metadata"]["page_number"] == 3
        assert src["metadata"]["locator"] == "page_3_p2"
    finally:
        await client.close()


async def test_log_event_endpoint_records_frontend_toast(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.post(
            "/api/logs/events",
            json={"type": "ok", "message": "已保存", "route": "/settings"},
        )
        assert resp.status == 200

        logs = await (await client.get("/api/logs?after=0&limit=20")).json()
        toast = next(line for line in logs["lines"] if line.get("category") == "toast")
        assert toast["source"] == "frontend"
        assert toast["operation"] == "notify"
        assert toast["status"] == "ok"
        assert toast["metadata"]["route"] == "/settings"
    finally:
        await client.close()


async def test_graph_mixed_ask_returns_structured_not_ready(tmp_path: Path) -> None:
    class Registry:
        def has_workspace(self, collection: str) -> bool:
            return False

    store = InMemorySourceDocumentStore()
    await store.add_document(
        SourceDocument("d1", "Doc 1", "/d1.pdf", "application/pdf", 1, "h", "papers")
    )
    api = KnowledgeRepositoryApi(
        source_store=store,
        lightrag_registry=Registry(),  # type: ignore[arg-type]
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/ask",
            json={
                "question": "q",
                "collection": "papers",
                "retrieval_mode": "graph_mixed",
            },
        )
        assert resp.status == 409
        assert await resp.json() == {
            "status": "lightrag_not_ready",
            "message": "LightRAG workspace has not been built.",
            "collection": "papers",
            "build_available": True,
        }
    finally:
        await client.close()


async def test_graph_mixed_ask_returns_renamed_failure_status(tmp_path: Path) -> None:
    """图谱 workspace 已就绪但查询失败时，HTTP 契约使用 graph_mixed_failed。"""
    from kacore.index_compatibility import IndexCompatibilityStore
    from kacore.pipelines.retrieval_orchestrator import RetrievalOutcome

    class Registry:
        def has_workspace(self, collection: str) -> bool:
            return collection == "papers"

    class Orchestrator:
        async def retrieve_with_outcome(
            self, collection, query, top_k, scope=None, candidate_k=None, reranker=None
        ):
            return RetrievalOutcome([], [])

        async def retrieve_lightrag_context(self, collection, query, scope=None) -> str:
            raise RuntimeError("graph query failed")

    store = InMemorySourceDocumentStore()
    await store.add_document(
        SourceDocument("d1", "Doc 1", "/d1.pdf", "application/pdf", 1, "h", "papers")
    )
    await store.set_lightrag_index_status("d1", "papers", "indexed")
    compatibility = IndexCompatibilityStore(tmp_path / "compat.json")
    compatibility.mark_lightrag_compatible("papers", "fp")
    api = KnowledgeRepositoryApi(
        source_store=store,
        lightrag_registry=Registry(),  # type: ignore[arg-type]
        retrieval_orchestrator=Orchestrator(),  # type: ignore[arg-type]
        index_compatibility=compatibility,
        embedding_fingerprint="fp",
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/ask",
            json={
                "question": "q",
                "collection": "papers",
                "retrieval_mode": "graph_mixed",
            },
        )
        assert resp.status == 502
        assert await resp.json() == {
            "status": "graph_mixed_failed",
            "message": "graph query failed",
            "collection": "papers",
        }
    finally:
        await client.close()


async def test_ask_progress_route_not_found(tmp_path: Path) -> None:
    """GET /api/ask/progress/{cid} — 不存在的 cid 应返回 404。"""
    from kacore.ask_progress import ProgressStore

    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(),
        progress_store=ProgressStore(),
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/ask/progress/nonexistent-cid")
        assert resp.status == 404
    finally:
        await client.close()


async def test_ask_progress_route_found(tmp_path: Path) -> None:
    """GET /api/ask/progress/{cid} — 已设置进度时应返回 stage + pct。"""
    from kacore.ask_progress import ProgressStore

    progress_store = ProgressStore()
    progress_store.set("test-cid-123", "vector_search", 20)

    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(),
        progress_store=progress_store,
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/ask/progress/test-cid-123")
        assert resp.status == 200
        body = await resp.json()
        assert body["stage"] == "vector_search"
        assert body["pct"] == 20
    finally:
        await client.close()


async def test_graph_stats_route(tmp_path: Path) -> None:
    """GET /api/graph/stats 应返回 entities_count / relations_count / collections_covered。"""
    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(),
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/graph/stats")
        assert resp.status == 200
        body = await resp.json()
        assert "entities_count" in body
        assert "relations_count" in body
        assert "collections_covered" in body
        assert isinstance(body["entities_count"], int)
    finally:
        await client.close()


# ── v1.0.0-rc.1 端点健壮性回归 ──────────────────────────────────


async def test_zotero_account_change_invalid_confirm_returns_400(tmp_path: Path) -> None:
    """无待确认变更 → ValueError → 400（而非 500）。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post(
            "/api/zotero/account-change",
            json={"change_id": "stale", "action": "replace_local"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert body["status"] == "error"
    finally:
        await client.close()


async def test_zotero_account_change_unexpected_error_returns_500_message(
    tmp_path: Path,
) -> None:
    """回归：意外异常曾进入引用未定义变量 collection 的 except 分支 → NameError。"""
    api = await _make_api()
    api.resolve_zotero_account_change = AsyncMock(  # type: ignore[method-assign]
        side_effect=OSError("boom")
    )
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/zotero/account-change", json={"change_id": "x", "action": "cancel"}
        )
        assert resp.status == 500
        body = await resp.json()
        assert body["message"] == "boom"  # 透传原始异常，而非 NameError
    finally:
        await client.close()


async def test_kb_search_invalid_top_k_returns_400(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/kb/search", params={"collection": "papers", "q": "alpha", "top_k": "abc"}
        )
        assert resp.status == 400
        body = await resp.json()
        assert "top_k" in body["error"]
    finally:
        await client.close()


async def test_kb_search_top_k_clamped(tmp_path: Path) -> None:
    """越界值夹取而非透传检索层。"""
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/kb/search",
            params={"collection": "papers", "q": "alpha", "top_k": "99999999"},
        )
        assert resp.status == 200
    finally:
        await client.close()


async def test_upload_over_size_limit_returns_413(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import web.server as server_mod

    monkeypatch.setattr(server_mod, "_MAX_UPLOAD_BYTES", 16)
    client = await _client(tmp_path)
    try:
        form = FormData()
        form.add_field(
            "file", b"x" * 64, filename="big.bin", content_type="application/octet-stream"
        )
        resp = await client.post("/api/documents", data=form)
        assert resp.status == 413
        # 流式暂存的 .part 临时文件必须被清理
        assert not list((tmp_path / "uploads").glob("*.part"))
    finally:
        await client.close()


async def test_upload_filename_is_sanitized(tmp_path: Path) -> None:
    """multipart filename 带字面路径分量时只保留 basename，杜绝目录穿越。"""
    client = await _client(tmp_path)
    try:
        boundary = "----kr-test-boundary"
        raw = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="../../evil.pdf"\r\n'
                "Content-Type: application/pdf\r\n\r\n"
            ).encode()
            + b"%PDF-1.4 fake"
            + f"\r\n--{boundary}--\r\n".encode()
        )
        resp = await client.post(
            "/api/documents",
            data=raw,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["title"] == "evil.pdf"
        assert not (tmp_path / "evil.pdf").exists()
    finally:
        await client.close()


async def test_notion_active_route_returns_null_when_idle(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/sync/notion/active")
        assert resp.status == 200
        assert await resp.json() == {"job": None}
    finally:
        await client.close()


# ── v1.0.10：Web 请求时间线与 seq 日志端点 ──────────────────────


async def test_mutating_request_is_recorded_even_when_web_logger_disabled(tmp_path: Path) -> None:
    api = await _make_api()
    log_handler = MemoryLogHandler()
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        log_handler=log_handler,
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    web_logger = logging.getLogger("KRWebServer")
    previous_disabled = web_logger.disabled
    web_logger.disabled = True
    await client.start_server()
    try:
        resp = await client.post("/api/documents/rebuild-index")
        assert resp.status == 503
        request_id = resp.headers["X-KA-Request-ID"]
        events = [
            line for line in log_handler.get_lines()
            if line["operation"] == "http_request"
            and line["metadata"].get("route") == "/api/documents/rebuild-index"
        ]
        assert [line["status"] for line in events] == ["started", "error"]
        assert {line["metadata"]["request_id"] for line in events} == {request_id}
        assert events[-1]["metadata"]["http_status"] == 503
        assert isinstance(events[-1]["elapsed_ms"], float)
    finally:
        web_logger.disabled = previous_disabled
        await client.close()


async def test_successful_poll_get_is_quiet_but_failed_mutation_is_not(tmp_path: Path) -> None:
    api = await _make_api()
    log_handler = MemoryLogHandler()
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        log_handler=log_handler,
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        assert (await client.get("/api/documents/rebuild-index/active")).status == 200
        assert log_handler.get_lines() == []
        assert (await client.post("/api/documents/rebuild-index")).status == 503
        assert len(log_handler.get_lines()) == 2
    finally:
        await client.close()


async def test_request_observability_never_logs_token_or_request_body(tmp_path: Path) -> None:
    api = await _make_api()
    log_handler = MemoryLogHandler()
    secret_token = "must-not-appear-in-terminal-token"
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        log_handler=log_handler,
        token=secret_token,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        secret_body = "must-not-appear-in-terminal-body"
        denied = await client.post(
            "/api/collections", json={"name": secret_body},
            headers={"Authorization": "Bearer wrong-secret-guess"},
        )
        assert denied.status == 401
        ok = await client.post(
            "/api/collections", json={"name": secret_body},
            headers={"Authorization": f"Bearer {secret_token}"},
        )
        assert ok.status in {200, 201}
        listed = await client.get(f"/api/collections?access_token={secret_token}")
        assert listed.status == 200
        serialized = str(log_handler.get_lines())
        assert secret_token not in serialized
        assert "wrong-secret-guess" not in serialized
        assert secret_body not in serialized
    finally:
        await client.close()


async def test_logs_endpoint_exposes_sequence_cursor_and_gap_fields(tmp_path: Path) -> None:
    api = await _make_api()
    log_handler = MemoryLogHandler(maxlen=3)
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        log_handler=log_handler,
        token=TOKEN,
    )
    for i in range(5):
        log_handler.add_event(
            source="test", category="system", operation="seed",
            status="ok", msg=f"line-{i}",
        )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        body = await (await client.get("/api/logs?after_seq=0&limit=2")).json()
        assert [line["seq"] for line in body["lines"]] == [4, 5]
        assert body["oldest_seq"] == 3
        assert body["latest_seq"] == 5
        assert body["dropped_count"] == 3
    finally:
        await client.close()

async def test_http_exception_response_carries_request_id(tmp_path: Path) -> None:
    api = await _make_api()
    log_handler = MemoryLogHandler()
    app = build_app(
        api=api,
        upload_dir=tmp_path / "uploads",
        log_handler=log_handler,
        token=TOKEN,
    )
    client = TestClient(TestServer(app), headers=AUTH)
    await client.start_server()
    try:
        resp = await client.get("/api/route-that-does-not-exist")
        assert resp.status == 404
        request_id = resp.headers["X-KA-Request-ID"]
        event = log_handler.get_lines()[-1]
        assert event["metadata"]["request_id"] == request_id
        assert event["metadata"]["http_status"] == 404
    finally:
        await client.close()


# ── /api/system/models ────────────────────────────────────────────


async def test_model_runtime_route_returns_accelerator_and_models(tmp_path: Path) -> None:
    """GET /api/system/models 应委派 api.get_model_runtime() 并返回稳定形状。"""
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/system/models")
        assert resp.status == 200
        body = await resp.json()
        assert "accelerator" in body  # 无 CUDA 时为 null，不是错误
        assert isinstance(body["models"], list)
        assert isinstance(body["resident_count"], int)
    finally:
        await client.close()


async def test_model_unload_route_accepts_empty_body(tmp_path: Path) -> None:
    """POST /api/system/models/unload 无 body 表示「全部卸载」，不得 400/500。"""
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/system/models/unload")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body["unloaded"], list)
        assert isinstance(body["errors"], dict)
    finally:
        await client.close()


async def test_model_unload_route_passes_kinds_through(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.post("/api/system/models/unload", json={"kinds": ["rerank"]})
        assert resp.status == 200
        assert "models" in await resp.json()
    finally:
        await client.close()


def test_document_ext_reports_the_real_type_of_original_pdf(tmp_path: Path) -> None:
    """本地上传的文本原件也存成 original.pdf：按文件头与标题后缀给出真实类型。"""
    from types import SimpleNamespace

    from web.server import _document_ext

    real = tmp_path / "a" / "original.pdf"
    real.parent.mkdir()
    real.write_bytes(b"%PDF-1.7\n...")
    text = tmp_path / "b" / "original.pdf"
    text.parent.mkdir()
    text.write_text("# notes\n")

    def doc(path: Path, title: str, ctype: str = "application/octet-stream") -> object:
        return SimpleNamespace(file_path=str(path), title=title, content_type=ctype)

    assert _document_ext(doc(real, "paper")) == "pdf"
    assert _document_ext(doc(text, "notes.md")) == "md"
    assert _document_ext(doc(text, "Clean Title")) == "txt"
    assert _document_ext(doc(text, "x", "application/pdf")) == "pdf"  # 声明为 PDF 时照旧
    assert _document_ext(doc(tmp_path / "zotero" / "paper.epub", "p")) == "epub"


async def test_get_single_document_route_does_not_shadow_static_routes(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/documents/d1")
        assert resp.status == 200 and (await resp.json())["doc_id"] == "d1"
        assert (await client.get("/api/documents/missing")).status == 404
        resp = await client.get("/api/documents/pending-reindex-count")
        assert resp.status == 200 and "doc_id" not in await resp.json()
    finally:
        await client.close()
