"""旧版插件数据导入器契约测试。

场景：在 tmp 目录里搭一份「旧安装」（插件数据目录 + 旧配置文件），验证
定位 → 预览 → staging（复制 / 改写路径 / 迁移 / 翻译）→ 换入 → 提交 / 回滚，以及经组合根
软重启的端到端导入。旧宿主名只经 `kacore.legacy_import.layout` 的常量拼出，本文件不直接书写。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from kacore.app_initializer import AppInitializer
from kacore.legacy_import.apply import (
    apply_pending_legacy_import,
    commit_applied_legacy_import,
    discard_pending_legacy_import,
    legacy_import_state,
    rollback_applied_legacy_import,
)
from kacore.legacy_import.layout import (
    PLUGIN_CONFIG_PARTS,
    PLUGIN_DATA_PARTS,
    LegacyImportError,
    locate,
)
from kacore.legacy_import.staging import (
    inspect_legacy_install,
    rewrite_legacy_path,
    stage_legacy_import,
    state_dir,
)
from kacore.migration_runner import MIGRATIONS_DIR
from kacore.settings.memory import MemorySecretStore
from kacore.settings.store import SettingsStore

OLD_ROOT_IN_DB = "/srv/old-host/" + "/".join(PLUGIN_DATA_PARTS)
LAST_MIGRATION = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))[-1].name


# ── 旧安装夹具 ──────────────────────────────────────────────────


def _old_database(path: Path) -> None:
    """按旧版缺最后一个迁移的 schema 建库，并写入两篇文档。"""
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))[:-1]
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
        for file in files:
            db.executescript(file.read_text(encoding="utf-8"))
            db.execute("INSERT INTO _migrations VALUES (?, 'old')", (file.name,))
        for doc_id, file_path in (
            ("d1", f"{OLD_ROOT_IN_DB}/library/d1/original.pdf"),
            ("d2", "/home/someone/Zotero/storage/ABC/linked.pdf"),
        ):
            db.execute(
                "INSERT INTO documents (doc_id, title, file_path, content_type, size_bytes,"
                " content_hash, collection, tags, created_at, updated_at)"
                " VALUES (?, ?, ?, 'application/pdf', 1, 'h', 'default', '[]', 't', 't')",
                (doc_id, doc_id, file_path),
            )
        db.commit()


@pytest.fixture
def old_install(tmp_path: Path) -> Path:
    root = tmp_path / "old-host"
    data = root.joinpath(*PLUGIN_DATA_PARTS)
    (data / "library" / "d1").mkdir(parents=True)
    (data / "library" / "d1" / "original.pdf").write_bytes(b"%PDF-1.4 old")
    (data / "library" / "d1" / "clean.md").write_text("# old", encoding="utf-8")
    (data / "lightrag_workspaces" / "default").mkdir(parents=True)
    (data / "lightrag_workspaces" / "default" / "graph.json").write_text("{}")
    (data / "vector_store.db").write_bytes(b"milvus-lite")
    (data / "index_compatibility.json").write_text(json.dumps({"milvus": {"fp": "x"}}))
    (data / "logs").mkdir()
    (data / "logs" / "old.log").write_text("noise")
    _old_database(data / "knowledge_repository.db")
    secrets = data / "secrets"
    secrets.mkdir()
    key = Fernet.generate_key()
    (secrets / "master.key").write_bytes(key)
    (secrets / "zotero.server_api_key.secret").write_bytes(Fernet(key).encrypt(b"zot-key-1234"))
    (data / "runtime_config.json").write_text(
        json.dumps({"graph": {"query_mode": "local"}}), encoding="utf-8"
    )
    config = root.joinpath(*PLUGIN_CONFIG_PARTS)
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "vector_db": {"backend": "lexical"},
                "graph": {"query_mode": "mix"},
                "embedding": {"provider": "hosted"},
                "notion_sync": {"enabled": False, "mcp_server_name": "notion"},
            }
        ),
        encoding="utf-8",
    )
    return root


def _digest(root: Path) -> str:
    sha = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            sha.update(str(path.relative_to(root)).encode())
            sha.update(path.read_bytes())
    return sha.hexdigest()


def _settings(data_dir: Path) -> SettingsStore:
    from kacore.settings.json_file import JsonFileSettingsBackend

    data_dir.mkdir(parents=True, exist_ok=True)
    return SettingsStore(JsonFileSettingsBackend(data_dir / "settings.json"), _SECRETS)


_SECRETS = MemorySecretStore()


@pytest.fixture(autouse=True)
def _fresh_secrets() -> None:
    _SECRETS._values.clear()  # type: ignore[attr-defined]


# ── 定位与预览 ──────────────────────────────────────────────────


def test_locate_accepts_host_root_data_dir_or_plugin_dir(old_install: Path) -> None:
    plugin_dir = old_install.joinpath(*PLUGIN_DATA_PARTS)
    for selected in (old_install, old_install / "data", plugin_dir):
        found = locate(selected)
        assert found.data_dir == plugin_dir.resolve()
        assert found.config_file is not None and found.runtime_config is not None


def test_locate_rejects_unrelated_folder(tmp_path: Path) -> None:
    with pytest.raises(LegacyImportError, match="旧版插件数据"):
        locate(tmp_path)
    with pytest.raises(LegacyImportError):
        locate(tmp_path / "missing")


def test_inspect_reports_contents(old_install: Path) -> None:
    preview = inspect_legacy_install(old_install)
    assert preview["document_count"] == 2
    assert preview["config_found"] and preview["runtime_config_found"]
    assert preview["has_vector_store"] and preview["has_graph"] and preview["has_secrets"]
    assert preview["file_count"] >= 5


def test_rewrite_legacy_path_handles_both_separators(tmp_path: Path) -> None:
    posix = f"{OLD_ROOT_IN_DB}/library/d1/original.pdf"
    windows = "C:\\Host\\" + "\\".join(PLUGIN_DATA_PARTS) + "\\library\\d1\\original.pdf"
    expected = str(tmp_path / "library" / "d1" / "original.pdf")
    assert rewrite_legacy_path(posix, tmp_path) == expected
    assert rewrite_legacy_path(windows, tmp_path) == expected
    assert rewrite_legacy_path("/home/x/Zotero/a.pdf", tmp_path) is None


# ── staging ─────────────────────────────────────────────────────


async def test_stage_copies_rewrites_migrates_and_translates(
    old_install: Path, tmp_path: Path
) -> None:
    before = _digest(old_install)
    data_dir = tmp_path / "new-data"
    data_dir.mkdir()
    stages: list[str] = []

    marker = await stage_legacy_import(
        old_install, data_dir, progress=lambda stage, _d, _t: stages.append(stage)
    )

    assert _digest(old_install) == before, "旧目录必须只读"
    payload = Path(marker["stage_root"]) / "payload"
    assert sorted(p.name for p in payload.iterdir()) == [
        "index_compatibility.json", "knowledge_repository.db", "library",
        "lightrag_workspaces", "vector_store.db",
    ]
    with closing(sqlite3.connect(payload / "knowledge_repository.db")) as db:
        paths = dict(db.execute("SELECT doc_id, file_path FROM documents").fetchall())
        applied = {row[0] for row in db.execute("SELECT name FROM _migrations")}
    assert paths["d1"] == str(data_dir / "library" / "d1" / "original.pdf")
    assert paths["d2"] == "/home/someone/Zotero/storage/ABC/linked.pdf"
    assert LAST_MIGRATION in applied
    summary = marker["summary"]
    assert summary["document_count"] == 2 and summary["paths_rewritten"] == 1
    assert summary["missing_originals"] == 1 and summary["migrations_applied"] == 1
    assert summary["secrets_imported"] == 1
    assert any("Embedding" in note for note in marker["attention"])
    assert any("链接模式" in note for note in marker["attention"])
    assert "notion_sync.mcp_server_name" in marker["dropped"]
    assert {"copying", "rewriting", "migrating"} <= set(stages)
    assert legacy_import_state(data_dir)["pending"]["import_id"] == marker["import_id"]


async def test_stage_refuses_second_pending_import(old_install: Path, tmp_path: Path) -> None:
    data_dir = tmp_path / "new-data"
    await stage_legacy_import(old_install, data_dir)
    with pytest.raises(LegacyImportError, match="等待应用"):
        await stage_legacy_import(old_install, data_dir)
    assert discard_pending_legacy_import(data_dir) is True
    assert legacy_import_state(data_dir)["pending"] is None
    assert not list((state_dir(data_dir) / "staging").iterdir())


async def test_stage_failure_removes_staging(old_install: Path, tmp_path: Path) -> None:
    db = old_install.joinpath(*PLUGIN_DATA_PARTS) / "knowledge_repository.db"
    db.write_bytes(b"not a database")
    data_dir = tmp_path / "new-data"
    with pytest.raises(LegacyImportError, match="准备导入时出错"):
        await stage_legacy_import(old_install, data_dir)
    assert legacy_import_state(data_dir)["pending"] is None
    assert not list((state_dir(data_dir) / "staging").iterdir())


async def test_stage_rejects_overlapping_directories(old_install: Path) -> None:
    plugin_dir = old_install.joinpath(*PLUGIN_DATA_PARTS)
    with pytest.raises(LegacyImportError, match="重叠"):
        await stage_legacy_import(old_install, plugin_dir / "inner")


# ── 换入 / 提交 / 回滚 ──────────────────────────────────────────


async def test_apply_then_commit(old_install: Path, tmp_path: Path) -> None:
    data_dir = tmp_path / "new-data"
    settings = _settings(data_dir)
    (data_dir / "knowledge_repository.db").write_bytes(b"empty new db")
    await stage_legacy_import(old_install, data_dir)

    assert apply_pending_legacy_import(data_dir, settings) is True
    with closing(sqlite3.connect(data_dir / "knowledge_repository.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    assert (data_dir / "library" / "d1" / "original.pdf").read_bytes() == b"%PDF-1.4 old"
    assert settings.get("flow.vector_store.backend") == "lexical"
    assert settings.get("flow.graph.query_mode") == "local"  # runtime_config 覆盖插件配置
    assert settings.get("flow.zotero.server_api_key") == "zot-key-1234"
    assert legacy_import_state(data_dir)["pending"] is not None  # 已换入、待确认

    assert commit_applied_legacy_import(data_dir) is True
    state = legacy_import_state(data_dir)
    assert state["pending"] is None and state["result"]["status"] == "ok"
    assert state["result"]["summary"]["secrets_imported"] == 1
    assert not (state_dir(data_dir) / "rollback").exists() or not list(
        (state_dir(data_dir) / "rollback").iterdir()
    )


async def test_rollback_restores_previous_state(old_install: Path, tmp_path: Path) -> None:
    data_dir = tmp_path / "new-data"
    settings = _settings(data_dir)
    settings.set("flow.graph.query_mode", "naive")
    (data_dir / "knowledge_repository.db").write_bytes(b"empty new db")
    await stage_legacy_import(old_install, data_dir)
    apply_pending_legacy_import(data_dir, settings)

    assert rollback_applied_legacy_import(data_dir, error="boom") is True
    assert (data_dir / "knowledge_repository.db").read_bytes() == b"empty new db"
    assert not (data_dir / "library").exists()
    settings.reload()
    assert settings.get("flow.graph.query_mode") == "naive"
    result = legacy_import_state(data_dir)["result"]
    assert result["status"] == "failed" and result["error"] == "boom"


def test_apply_without_pending_is_noop(tmp_path: Path) -> None:
    assert apply_pending_legacy_import(tmp_path, _settings(tmp_path)) is False
    assert commit_applied_legacy_import(tmp_path) is False
    assert rollback_applied_legacy_import(tmp_path) is False


# ── 端到端：API → staging → 软重启换入 ─────────────────────────────


async def _wait_for(predicate, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timeout")
        await asyncio.sleep(0.05)


async def test_api_import_end_to_end_through_soft_restart(
    old_install: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "new-data"
    settings = SettingsStore.in_memory({"flow.vector_store.backend": "lexical"})
    init = AppInitializer(settings, data_dir)
    await init.initialize()
    try:
        api = init.api
        assert api is not None
        status = await api.get_legacy_import_status()
        assert status["library_empty"] is True and status["pending"] is None

        preview = await api.inspect_legacy_data(str(old_install))
        assert preview["document_count"] == 2
        job = await api.start_legacy_import(str(old_install))
        assert job["status"] == "running"

        await _wait_for(lambda: init.api is not None and init.api is not api)
        await _wait_for(lambda: init.api is not None)
        new_api = init.api
        assert new_api is not None
        docs = await new_api.list_documents()
        assert {d.doc_id for d in docs} == {"d1", "d2"}
        status = await new_api.get_legacy_import_status()
        assert status["result"]["status"] == "ok" and status["library_empty"] is False
        assert settings.get("flow.graph.query_mode") == "local"

        from kacore.api_legacy_import import LegacyImportConflictError

        with pytest.raises(LegacyImportConflictError, match="空库"):
            await new_api.start_legacy_import(str(old_install))
    finally:
        await init.teardown()


async def test_api_rejects_bad_path_immediately(tmp_path: Path) -> None:
    settings = SettingsStore.in_memory({"flow.vector_store.backend": "lexical"})
    init = AppInitializer(settings, tmp_path / "data")
    await init.initialize()
    try:
        assert init.api is not None
        with pytest.raises(LegacyImportError):
            await init.api.start_legacy_import(str(tmp_path / "nothing-here"))
        assert (await init.api.get_legacy_import_status())["job"] is None
    finally:
        await init.teardown()


async def test_http_routes(old_install: Path, tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from kacore.api import KnowledgeRepositoryApi
    from kacore.domain.models import SourceDocument
    from kacore.repository.source_store.memory import InMemorySourceDocumentStore
    from web.server import build_app

    store = InMemorySourceDocumentStore()
    api = KnowledgeRepositoryApi(source_store=store, data_dir=tmp_path / "data")
    client = TestClient(
        TestServer(build_app(api=api, upload_dir=tmp_path / "u", token="t")),
        headers={"Authorization": "Bearer t"},
    )
    await client.start_server()
    try:
        assert (await client.post("/api/legacy-import/inspect", json={})).status == 400
        bad = await client.post("/api/legacy-import/inspect", json={"path": str(tmp_path)})
        assert bad.status == 400 and "旧版插件数据" in (await bad.json())["message"]
        ok = await client.post("/api/legacy-import/inspect", json={"path": str(old_install)})
        assert ok.status == 200 and (await ok.json())["document_count"] == 2
        status = await (await client.get("/api/legacy-import")).json()
        assert status["library_empty"] is True and status["job"] is None
        await store.add_document(SourceDocument("x", "x", "/x.pdf", "pdf", 1, "h", "default"))
        conflict = await client.post("/api/legacy-import", json={"path": str(old_install)})
        assert conflict.status == 409
        assert (await client.delete("/api/legacy-import")).status == 200
    finally:
        await client.close()
