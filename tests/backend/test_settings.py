"""设置子系统：SettingSpec 登记表、SettingsStore、Config 桥、旧键翻译、主密钥、R2 可携设置。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kacore.config import Config
from kacore.index_compatibility import embedding_fingerprint
from kacore.providers.base import ProviderProfile, api_key_ref_for
from kacore.secret_store import KEYRING_SERVICE, EncryptedSecretStore
from kacore.settings.base import SettingsValidationError
from kacore.settings.legacy_keys import apply_translation, translate
from kacore.settings.memory import MemorySecretStore
from kacore.settings.schema import (
    FLOW_HOMES,
    PREFS_HOMES,
    SPECS,
    SPECS_BY_KEY,
    spec_for,
    specs_for_home,
)
from kacore.settings.store import SettingsStore, mask_secret, normalize_value

# ── 登记表 ───────────────────────────────────────────────────────


def test_every_setting_is_registered_exactly_once_with_a_valid_default() -> None:
    assert len(SPECS_BY_KEY) == len(SPECS)
    targets = [target for spec in SPECS for target in spec.target]
    assert len(targets) == len(set(targets)), "一个旧 (section, key) 只能归属一个设置项"
    for spec in SPECS:
        assert spec.key.startswith(("flow.", "prefs.")) and spec.home in spec.key
        if spec.kind != "secret":
            assert normalize_value(spec, spec.default) == spec.default, spec.key
        assert spec.label_key == "set_" + spec.key.replace(".", "_")


def test_homes_cover_the_data_flow_nodes_and_preference_groups() -> None:
    assert set(FLOW_HOMES) == {
        "flow.models", "flow.import", "flow.zotero", "flow.embedding", "flow.vector_store",
        "flow.graph", "flow.retrieval", "flow.answer", "flow.notion", "flow.r2",
    }  # 第 11 个节点「模型供应商」是连接列表，不是 SettingSpec
    assert set(PREFS_HOMES) == {
        "prefs.general", "prefs.appearance", "prefs.storage", "prefs.local_models",
        "prefs.diagnostics",
    }
    assert {s.key for s in specs_for_home("flow.graph")} >= {
        "flow.graph.enabled", "flow.graph.query_mode"
    }
    assert all(s.home.startswith("prefs.") for s in specs_for_home("prefs"))


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("flow.graph.query_mode", "bogus", "must be one of"),
        ("flow.graph.enabled", "yes", "boolean"),
        ("flow.zotero.api_port", 70000, "out of range"),
        ("flow.zotero.api_port", 1.5, "integer"),
        ("flow.retrieval.deep_thinking.rerank_weight", float("nan"), "finite"),
        ("flow.models.chat", {"provider_id": "x"}, "provider_id"),
        ("flow.embedding.source", "remote", "provider_id"),
    ],
)
def test_invalid_values_are_rejected(key: str, value: Any, message: str) -> None:
    with pytest.raises(SettingsValidationError, match=message):
        normalize_value(spec_for(key), value)


def test_model_ref_and_literal_options_normalize() -> None:
    source = spec_for("flow.embedding.source")
    assert normalize_value(source, "local") == "local"
    assert normalize_value(source, {"provider_id": " a ", "model": " m "}) == {
        "provider_id": "a",
        "model": "m",
    }
    assert normalize_value(spec_for("flow.models.research"), None) is None


# ── SettingsStore ────────────────────────────────────────────────


def _file_store(tmp_path: Path) -> tuple[SettingsStore, MemorySecretStore]:
    secrets = MemorySecretStore()
    return SettingsStore.open(tmp_path / "settings.json", secrets), secrets


def test_values_persist_and_defaults_are_not_written(tmp_path: Path) -> None:
    store, secrets = _file_store(tmp_path)
    assert not store.persisted
    change = store.set("flow.graph.query_mode", "local")
    assert change.changed and change.effective_consequence == "restart"
    again = store.set("flow.graph.query_mode", "local")
    assert not again.changed and again.effective_consequence == "none"

    reopened = SettingsStore.open(tmp_path / "settings.json", secrets)
    assert reopened.persisted
    assert reopened.get("flow.graph.query_mode") == "local"
    document = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert set(document["values"]) == {"flow.graph.query_mode"}


def test_secrets_live_only_in_the_secret_store(tmp_path: Path) -> None:
    store, secrets = _file_store(tmp_path)
    store.set("flow.r2.secret_access_key", "  super-secret-value  ")
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "super-secret-value" not in raw
    assert json.loads(raw)["values"]["flow.r2.secret_access_key"] == {
        "ref": "flow.r2.secret_access_key"
    }
    assert secrets.get_secret("flow.r2.secret_access_key") == "super-secret-value"
    assert store.public_value("flow.r2.secret_access_key") == {
        "set": True,
        "masked": mask_secret("super-secret-value"),
    }
    assert "super-secret-value" not in json.dumps(store.schema("flow.r2"))
    store.set("flow.r2.secret_access_key", "")
    assert not secrets.has_secret("flow.r2.secret_access_key")
    assert "flow.r2.secret_access_key" not in json.loads(
        (tmp_path / "settings.json").read_text(encoding="utf-8")
    )["values"]


def test_zotero_server_key_keeps_its_existing_secret_name() -> None:
    secrets = MemorySecretStore({"zotero.server_api_key": "legacy-zotero-key"})
    store = SettingsStore.in_memory(secrets=secrets)
    assert store.get("flow.zotero.server_api_key") == "legacy-zotero-key"


def test_set_many_validates_everything_before_writing(tmp_path: Path) -> None:
    store, secrets = _file_store(tmp_path)
    with pytest.raises(SettingsValidationError):
        store.set_many({"flow.r2.access_key_id": "AKIA-should-not-land", "flow.graph.enabled": 3})
    assert not secrets.has_secret("flow.r2.access_key_id")
    assert not (tmp_path / "settings.json").exists()
    with pytest.raises(KeyError):
        store.set("flow.unknown.key", 1)


def test_reset_restores_default_and_corrupt_file_falls_back(tmp_path: Path) -> None:
    store, secrets = _file_store(tmp_path)
    store.set("flow.answer.language", "en")
    assert store.reset("flow.answer.language").changed
    assert store.get("flow.answer.language") == "auto"
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert SettingsStore.open(tmp_path / "settings.json", secrets).get(
        "flow.answer.language"
    ) == "auto"


def test_mask_secret_shows_only_the_last_four_characters() -> None:
    assert mask_secret("sk-1234567890abcd") == "••••abcd"
    assert mask_secret("short") == "••••"
    assert mask_secret("") == ""


# ── Config 桥 ────────────────────────────────────────────────────


def test_bridge_maps_targets_and_merged_device() -> None:
    store = SettingsStore.in_memory({
        "flow.graph.query_mode": "global",
        "prefs.local_models.device": "cpu",
        "flow.r2.access_key_id": "ak",
        "flow.answer.system_prompt": "You are terse.",
    })
    cfg = Config(store.to_config_raw())
    assert cfg.get_graph_config().query_mode == "global"
    assert cfg.get_embedding_config().device == "cpu"
    assert cfg.get_rerank_config().device == "cpu"
    assert cfg.get_r2_sync_config().access_key_id == "ak"
    assert cfg.get_ask_agent_config().system_prompt == "You are terse."


def test_bridge_keeps_frozen_fingerprints_for_local_and_provider_backed_external() -> None:
    local = SettingsStore.in_memory({"flow.embedding.source": "local"})
    local_cfg = Config(local.to_config_raw()).get_embedding_config()
    assert embedding_fingerprint(local_cfg, 384) == (
        "901fa583ba316a6ba3174b92a009840be8e4708296a12de9b23e8dba41a5351e"
    )

    external = SettingsStore.in_memory()
    profile = ProviderProfile(
        "example", "openai_compatible", "Example", "https://api.example.test/v1",
        api_key_ref_for("example"),
    )
    external.set_providers([profile.to_dict()])
    external.secrets.set_secret(profile.api_key_ref, "emb-key")
    external.set(
        "flow.embedding.source", {"provider_id": "example", "model": "text-embedding-3-small"}
    )
    ext_cfg = Config(external.to_config_raw()).get_embedding_config()
    assert (ext_cfg.provider, ext_cfg.api_key) == ("external", "emb-key")
    assert embedding_fingerprint(ext_cfg, 1536) == (
        "70a10d12bb9494ebd1dacea230f7d594fb38f1eb14c8a6e6f4c0c30db3166304"
    )


def test_bridge_reports_model_assignments() -> None:
    store = SettingsStore.in_memory()
    store.set_providers([
        ProviderProfile("lmstudio", "openai_compatible", "LM", "http://localhost:1234/v1").to_dict()
    ])
    store.set("flow.models.chat", {"provider_id": "lmstudio", "model": "qwen"})
    models = Config(store.to_config_raw()).get_model_assignments()
    assert (models.chat, models.research, models.graph) == ("lmstudio:qwen", "", "")
    assert models.graph_is_local is True  # graph 跟随 chat，落在本机


# ── 旧键翻译 ─────────────────────────────────────────────────────

_HOST_PROVIDER_ID_KEY = "astr" + "bot_provider_id"


def _legacy_plugin_config() -> dict[str, Any]:
    return {
        "web_console": {"enabled": True, "password": "pw", "port": 26618},
        "source_store": {"default_collection": "papers", "ocr_enabled": True},
        "vector_db": {"backend": "milvus", "auto_rebuild_delay_seconds": 2},
        "embedding": {"provider": "local", "model": "BAAI/bge-m3", "device": "cuda:1",
                      _HOST_PROVIDER_ID_KEY: "emb-a"},
        "rerank": {"provider": "auto", "device": "cpu"},
        "graph": {"enabled": True, "query_mode": "hybrid",
                  "lightrag_llm_base_url": "http://localhost:1234/v1",
                  "lightrag_llm_model": "phi4"},
        "deep_thinking": {"max_rounds": 6, "llm_base_url": "https://api.deepseek.com/v1",
                          "llm_model": "deepseek-chat", "llm_api_key": "sk-deep-0001"},
        "ask": {"answer_language": "zh", "agent_enabled": True, "persona_enabled": True},
        "notion_sync": {"enabled": True, "mcp_server_name": "notion", "database_id": "db1"},
        "r2_sync": {"enabled": True, "account_id": "acc", "access_key_id": "ak",
                    "secret_access_key": "sk-r2", "bucket": "b"},
        "zotero_sync": {"enabled": True, "sync_mode": "archive", "auto_sync_interval_sec": 10},
    }


def test_translate_renames_keys_and_reports_dropped_ones(monkeypatch) -> None:
    envs = ("KR_DEEP_THINKING_LLM_API_KEY", "KR_LIGHTRAG_LLM_API_KEY", "KR_R2_SECRET_ACCESS_KEY")
    for env in envs:
        monkeypatch.delenv(env, raising=False)
    result = translate(_legacy_plugin_config())
    values = result.values
    assert values["flow.import.default_collection"] == "papers"
    assert values["flow.import.ocr_enabled"] is True
    assert values["flow.vector_store.auto_rebuild_delay_sec"] == 5  # 旧值按既有规则钳到下限
    assert values["flow.embedding.local_model"] == "BAAI/bge-m3"
    assert values["prefs.local_models.device"] == "cuda"  # 两个 device 合并，embedding 优先
    assert values["flow.retrieval.rerank.provider"] == "auto"  # 原始值，不是运行期解析结果
    assert values["flow.graph.query_mode"] == "hybrid"
    assert values["flow.retrieval.deep_thinking.max_rounds"] == 6
    assert values["flow.answer.language"] == "zh"
    assert values["flow.notion.database_id"] == "db1"
    assert values["flow.zotero.sync_mode"] == "archive"
    assert values["flow.zotero.auto_sync_interval_sec"] == 60

    providers = {p["id"]: p for p in result.providers}
    assert providers["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    assert providers["lmstudio"]["base_url"] == "http://localhost:1234/v1"
    assert values["flow.models.research"] == {"provider_id": "deepseek", "model": "deepseek-chat"}
    assert values["flow.models.graph"] == {"provider_id": "lmstudio", "model": "phi4"}
    assert result.secrets == {
        "provider.deepseek.api_key": "sk-deep-0001",
        "flow.r2.access_key_id": "ak",
        "flow.r2.secret_access_key": "sk-r2",
    }
    assert any("lmstudio" in note for note in result.attention)  # 本机服务可留空密钥
    assert set(result.dropped) >= {
        "web_console.enabled", "web_console.password", "web_console.port",
        f"embedding.{_HOST_PROVIDER_ID_KEY}", "ask.agent_enabled", "ask.persona_enabled",
        "notion_sync.mcp_server_name",
    }


def test_translate_handles_external_and_removed_embedding_sources(monkeypatch) -> None:
    monkeypatch.delenv("KR_EMBEDDING_API_KEY", raising=False)
    external = translate({"embedding": {"provider": "external", "model": "text-embedding-3-small",
                                        "base_url": "https://api.openai.com/v1"}})
    assert external.values["flow.embedding.source"] == {
        "provider_id": "openai", "model": "text-embedding-3-small"
    }
    assert any("API key" in note for note in external.attention)

    monkeypatch.setenv("KR_EMBEDDING_API_KEY", "env-emb-key")
    with_key = translate({"embedding": {"provider": "external", "model": "m",
                                        "base_url": "https://emb.example/v1"}})
    assert with_key.secrets == {"provider.emb-example.api_key": "env-emb-key"}

    removed = translate({"embedding": {"provider": "astr", "model": "ignored"}})
    assert removed.values["flow.embedding.source"] == "local"  # 旧版默认本地，迁移后保持
    assert "flow.embedding.local_model" not in removed.values
    assert any("was removed" in note for note in removed.attention)


def test_translate_moves_lexical_only_backend_to_milvus() -> None:
    for legacy in ("lexical", "astr"):
        result = translate({"vector_db": {"backend": legacy}})
        assert result.values["flow.vector_store.backend"] == "milvus"
        assert any("Milvus Lite" in note for note in result.attention)
    assert not any("Milvus Lite" in note for note in translate({"vector_db": {}}).attention)


def test_apply_translation_round_trips_into_config(monkeypatch) -> None:
    monkeypatch.delenv("KR_R2_SECRET_ACCESS_KEY", raising=False)
    store = SettingsStore.in_memory()
    apply_translation(store, translate(_legacy_plugin_config()))
    cfg = Config(store.to_config_raw())
    assert cfg.get_r2_sync_config().secret_access_key == "sk-r2"
    assert cfg.get_graph_config().enabled is True
    assert cfg.get_model_assignments().research == "deepseek:deepseek-chat"
    assert store.secrets.get_secret("provider.deepseek.api_key") == "sk-deep-0001"


# ── 主密钥：系统钥匙串优先 ─────────────────────────────────────────


class _FakeKeyring:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("no keyring daemon")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("no keyring daemon")
        self.values[(service, username)] = password


def test_new_master_key_goes_to_the_keyring_not_a_file(tmp_path: Path) -> None:
    ring = _FakeKeyring()
    store = EncryptedSecretStore(tmp_path / "secrets", keyring_backend=ring)
    store.set_secret("provider.x.api_key", "value-1")
    assert not (tmp_path / "secrets" / "master.key").exists()
    assert [service for service, _ in ring.values] == [KEYRING_SERVICE]
    reopened = EncryptedSecretStore(tmp_path / "secrets", keyring_backend=ring)
    assert reopened.get_secret("provider.x.api_key") == "value-1"
    assert reopened.uses_keyring


def test_existing_file_key_migrates_into_the_keyring(tmp_path: Path) -> None:
    EncryptedSecretStore(tmp_path / "secrets").set_secret("zotero.server_api_key", "zk")
    assert (tmp_path / "secrets" / "master.key").exists()
    ring = _FakeKeyring()
    migrated = EncryptedSecretStore(tmp_path / "secrets", keyring_backend=ring)
    assert migrated.get_secret("zotero.server_api_key") == "zk"
    assert not (tmp_path / "secrets" / "master.key").exists()
    assert ring.values


def test_broken_keyring_falls_back_to_the_file_key(tmp_path: Path) -> None:
    store = EncryptedSecretStore(tmp_path / "secrets", keyring_backend=_FakeKeyring(fail=True))
    store.set_secret("flow.notion.token", "secret_x")
    assert (tmp_path / "secrets" / "master.key").exists()
    assert EncryptedSecretStore(tmp_path / "secrets").get_secret("flow.notion.token") == "secret_x"


# ── R2 可携设置 ──────────────────────────────────────────────────


def test_portable_document_excludes_secrets_local_paths_and_r2() -> None:
    store = SettingsStore.in_memory({
        "flow.graph.query_mode": "local",
        "flow.zotero.data_dir": "/home/me/Zotero",
        "flow.r2.bucket": "b",
        "flow.r2.access_key_id": "ak",
        "prefs.local_models.device": "cuda",
    })
    store.set_providers([ProviderProfile("p", "openai_compatible", "P", "https://p/v1",
                                         api_key_ref_for("p")).to_dict()])
    store.secrets.set_secret(api_key_ref_for("p"), "provider-secret")
    portable = store.portable_document()
    assert portable["values"] == {"flow.graph.query_mode": "local"}
    assert portable["providers"][0]["id"] == "p"
    assert "provider-secret" not in json.dumps(portable)

    target = SettingsStore.in_memory({"flow.zotero.data_dir": "/mine"})
    ignored = target.merge_portable({**portable, "values": {**portable["values"],
                                                            "flow.r2.bucket": "x", "bogus": 1}})
    assert target.get("flow.graph.query_mode") == "local"
    assert target.get("flow.zotero.data_dir") == "/mine"
    assert set(ignored) == {"flow.r2.bucket", "bogus"}


def test_legacy_snapshot_portable_config_is_translated_on_restore(tmp_path: Path) -> None:
    from kacore.managers.r2_restore import _stage_merged_settings

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "payload" / "config"
    config_dir.mkdir(parents=True)
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({
        "version": 1, "values": {"flow.answer.language": "en"}, "providers": []
    }), encoding="utf-8")
    (config_dir / "portable_config.json").write_text(json.dumps({
        "graph": {"query_mode": "naive"}, "ask": {"persona_enabled": True},
    }), encoding="utf-8")
    staged = config_dir / "settings.json"
    _stage_merged_settings(data_dir, config_dir, staged)
    merged = json.loads(staged.read_text(encoding="utf-8"))["values"]
    # 旧快照没写 embedding：旧版默认本地，迁移后保持本地（新版默认线上）。
    assert merged == {
        "flow.answer.language": "en", "flow.graph.query_mode": "naive",
        "flow.embedding.source": "local",
    }


def test_fresh_settings_default_to_online_embedding_and_rerank() -> None:
    """新版默认线上：未选连接时 Embedding 待配置（不探针）、Rerank 不重排。"""
    cfg = Config(SettingsStore.in_memory().to_config_raw())
    emb = cfg.get_embedding_config()
    assert (emb.provider, emb.base_url, emb.model) == ("external", "", "")
    assert cfg.get_rerank_config().provider == "noop"

    store = SettingsStore.in_memory()
    store.set_providers([ProviderProfile(
        "jina", "openai_compatible", "Jina", "https://api.jina.ai/v1", api_key_ref_for("jina")
    ).to_dict()])
    store.set("flow.embedding.source", {"provider_id": "jina", "model": "jina-embeddings-v3"})
    store.set("flow.retrieval.rerank.remote", {"provider_id": "jina", "model": "jina-reranker-v2"})
    cfg = Config(store.to_config_raw())
    assert cfg.get_embedding_config().base_url == "https://api.jina.ai/v1"
    assert cfg.get_rerank_config().provider == "remote"  # 默认 provider 即 remote
