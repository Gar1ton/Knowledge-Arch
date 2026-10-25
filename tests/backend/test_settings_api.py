"""设置 / 连接的业务门面与 HTTP 路由；ka_server 首启迁移旧 runtime_config.json。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ka_server.__main__ import migrate_legacy_runtime_config, open_settings
from ka_server.paths import AppPaths
from kacore.api import KnowledgeRepositoryApi
from kacore.config import Config
from kacore.domain.models import DocumentChunk, SourceDocument
from kacore.index_compatibility import IndexCompatibilityStore
from kacore.providers.registry import ProviderRegistry
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.settings.store import SettingsStore
from web.server import build_app

TOKEN = "settings-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _api(tmp_path: Path) -> tuple[KnowledgeRepositoryApi, SettingsStore, Any]:
    from kacore.repository.vector_store.memory import InMemoryVectorStore
    from tests.backend.test_embedding import MockEmbeddingProvider

    settings = SettingsStore.in_memory({"flow.vector_store.backend": "milvus"})
    store = InMemorySourceDocumentStore()
    await store.add_document(SourceDocument("d1", "d1", "/d1.pdf", "application/pdf", 1, "h", "p"))
    await store.replace_chunks("d1", [DocumentChunk("c1", "d1", 0, "evidence", "h1")])
    compat = IndexCompatibilityStore(tmp_path / "compat.json")
    compat.mark_milvus_compatible("fp")
    api = KnowledgeRepositoryApi(
        source_store=store,
        config=Config(settings.to_config_raw()),
        settings_store=settings,
        provider_registry=ProviderRegistry(settings),
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(dimension=4),
        index_compatibility=compat,
        embedding_fingerprint="fp",
    )
    return api, settings, compat


async def test_update_setting_reports_consequence_and_refreshes_config(tmp_path: Path) -> None:
    api, settings, compat = await _api(tmp_path)
    result = await api.update_setting("flow.graph.query_mode", "local")
    assert result["consequence"] == "restart" and result["restart_required"]
    assert api._config.get_graph_config().query_mode == "local"
    same = await api.update_setting("flow.graph.query_mode", "local")
    assert same["consequence"] == "none"
    hot = await api.update_setting("flow.answer.language", "en")
    assert hot["consequence"] == "none"
    assert api._config.get_ask_agent_config().answer_language == "en"
    assert compat.is_milvus_compatible("fp")
    with pytest.raises(ValueError, match="未知设置项"):
        await api.update_setting("flow.nope", 1)
    with pytest.raises(ValueError, match="只能是"):
        await api.update_setting("flow.graph.query_mode", "bogus")


async def test_embedding_source_change_invalidates_indexes(tmp_path: Path) -> None:
    api, settings, compat = await _api(tmp_path)
    api.create_provider({"label": "Emb", "base_url": "https://emb.example/v1", "id": "emb"})
    result = await api.update_setting(
        "flow.embedding.source", {"provider_id": "emb", "model": "text-embedding-3-small"}
    )
    assert result["rebuild_required"] is True
    assert not compat.is_milvus_compatible("fp")
    emb = api._config.get_embedding_config()
    assert (emb.provider, emb.base_url, emb.model) == (
        "external", "https://emb.example/v1", "text-embedding-3-small"
    )

    api.create_provider({"label": "Claude", "base_url": "https://api.anthropic.com",
                         "kind": "anthropic", "id": "claude"})
    with pytest.raises(ValueError, match="OpenAI 兼容"):
        await api.update_setting("flow.embedding.source", {"provider_id": "claude", "model": "x"})
    with pytest.raises(ValueError, match="不存在"):
        await api.update_setting("flow.models.chat", {"provider_id": "ghost", "model": "x"})


async def test_moving_the_embedding_connection_requires_rebuild(tmp_path: Path) -> None:
    api, settings, compat = await _api(tmp_path)
    api.create_provider({"label": "Emb", "base_url": "https://emb.example/v1", "id": "emb"})
    await api.update_setting("flow.embedding.source", {"provider_id": "emb", "model": "m"})
    compat.mark_milvus_compatible("fp")
    renamed = await api.update_provider("emb", {"label": "Renamed"})
    assert renamed["rebuild_required"] is False and compat.is_milvus_compatible("fp")
    moved = await api.update_provider("emb", {"base_url": "https://emb2.example/v1"})
    assert moved["rebuild_required"] is True and not compat.is_milvus_compatible("fp")
    keyed = await api.update_provider("emb", {"api_key": "new-key"})
    assert keyed["restart_required"] is True
    assert api._config.get_embedding_config().api_key == "new-key"


async def test_legacy_config_update_maps_onto_settings(tmp_path: Path) -> None:
    api, settings, _ = await _api(tmp_path)
    result = await api.update_config_value("graph", "query_mode", "naive")
    assert result["restart_required"] is True
    assert settings.get("flow.graph.query_mode") == "naive"
    await api.update_config_value("embedding", "model", "BAAI/bge-m3")
    assert settings.get("flow.embedding.local_model") == "BAAI/bge-m3"
    with pytest.raises(ValueError, match="模型供应商"):
        await api.update_config_value("embedding", "provider", "external")
    with pytest.raises(ValueError, match="机密字段"):
        await api.update_config_value("r2_sync", "secret_access_key", "x")
    with pytest.raises(ValueError, match="not allowed"):
        await api.update_config_value("graph", "working_dir", "x")


async def _client(tmp_path: Path) -> tuple[TestClient, SettingsStore]:
    api, settings, _ = await _api(tmp_path)
    client = TestClient(
        TestServer(build_app(api=api, upload_dir=tmp_path, token=TOKEN)), headers=AUTH
    )
    await client.start_server()
    return client, settings


async def test_settings_routes(tmp_path: Path) -> None:
    client, settings = await _client(tmp_path)
    try:
        schema = await (await client.get("/api/settings/schema?home=flow.graph")).json()
        keys = {item["key"] for item in schema["items"]}
        assert "flow.graph.query_mode" in keys and all(k.startswith("flow.graph.") for k in keys)
        assert "flow.models" in schema["flow_homes"]

        ok = await client.patch("/api/settings", json={"key": "flow.graph.enabled", "value": True})
        assert ok.status == 200 and (await ok.json())["consequence"] == "restart"
        bad = await client.patch("/api/settings", json={"key": "flow.graph.enabled", "value": 1})
        assert bad.status == 400
        missing = await client.patch("/api/settings", json={"value": 1})
        assert missing.status == 400

        secret = await client.patch(
            "/api/settings", json={"key": "flow.r2.secret_access_key", "value": "r2-secret-123456"}
        )
        body = await secret.json()
        assert body["value"] == {"set": True, "masked": "••••3456"}
        assert "r2-secret-123456" not in json.dumps(
            await (await client.get("/api/settings/schema?home=flow.r2")).json()
        )
    finally:
        await client.close()


async def test_provider_routes(tmp_path: Path) -> None:
    client, settings = await _client(tmp_path)
    try:
        listing = await (await client.get("/api/providers")).json()
        assert listing["providers"] == []
        preset_ids = {p["id"] for p in listing["presets"]}
        assert preset_ids >= {"openai", "anthropic", "deepseek", "ollama"}

        created = await client.post("/api/providers", json={
            "label": "Local", "base_url": "http://127.0.0.1:9/v1", "id": "local", "api_key": "",
        })
        assert created.status == 201 and (await created.json())["id"] == "local"
        bad = await client.post("/api/providers", json={"label": "x", "base_url": "nope"})
        assert bad.status == 400

        tested = await (await client.post("/api/providers/local/test")).json()
        assert tested["status"] == "error" and tested["code"] == "unreachable"
        models = await (await client.post("/api/providers/local/models")).json()
        assert models["status"] == "error"
        assert (await client.post("/api/providers/ghost/test")).status == 404

        patched = await client.patch("/api/providers/local", json={"api_key": "sk-local-99999"})
        assert (await patched.json())["api_key_masked"] == "••••9999"
        settings.set("flow.models.chat", {"provider_id": "local", "model": "m"})
        in_use = await client.delete("/api/providers/local")
        assert in_use.status == 400
        settings.set("flow.models.chat", None)
        assert (await client.delete("/api/providers/local")).status == 200
    finally:
        await client.close()


async def test_settings_routes_without_a_store_return_501(tmp_path: Path) -> None:
    api = KnowledgeRepositoryApi(source_store=InMemorySourceDocumentStore())
    client = TestClient(
        TestServer(build_app(api=api, upload_dir=tmp_path, token=TOKEN)), headers=AUTH
    )
    await client.start_server()
    try:
        assert (await client.get("/api/settings/schema")).status == 501
        assert (await client.get("/api/providers")).status == 501
    finally:
        await client.close()


# ── ka_server 首启迁移 ───────────────────────────────────────────


def test_first_start_migrates_a_lone_legacy_runtime_config(tmp_path: Path) -> None:
    paths = AppPaths.resolve(tmp_path / "data")
    paths.ensure()
    legacy = {"graph": {"query_mode": "global"}, "notion_sync": {"database_id": "db-1"},
              "ask": {"agent_enabled": True}}
    (paths.data / "runtime_config.json").write_text(json.dumps(legacy), encoding="utf-8")

    settings = open_settings(paths)
    assert migrate_legacy_runtime_config(paths, settings) is True
    assert paths.settings_file.exists()
    reopened = open_settings(paths)
    assert reopened.get("flow.graph.query_mode") == "global"
    assert reopened.get("flow.notion.database_id") == "db-1"
    assert (paths.data / "runtime_config.json").exists()  # 旧文件保留，只读来源
    assert migrate_legacy_runtime_config(paths, reopened) is False  # 已有 settings.json 不再触发
