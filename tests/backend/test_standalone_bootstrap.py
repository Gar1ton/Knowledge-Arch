"""独立壳（ka_standalone）引导契约测试：数据目录解析、配置引导、组合根注入。

组合根注入的回归约束：不传覆盖参数时行为与插件壳逐字节一致（默认构造
AstrBotKnowledgeBaseReader / LLMAdapter(context)），传入时全部生效。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ka_core.config import ENV_MAIN_LLM_API_KEY, Config
from ka_core.plugin_initializer import PluginInitializer
from ka_core.repository.kb_reader.memory import InMemoryKnowledgeBaseReader
from ka_standalone.bootstrap import build_initializer, resolve_main_llm_adapter
from ka_standalone.config_bootstrap import CONFIG_FILENAME, load_or_create
from ka_standalone.paths import ENV_DATA_DIR, default_data_dir, resolve_data_dir

# ── paths ────────────────────────────────────────────────────


def test_resolve_data_dir_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "from-env"))
    assert resolve_data_dir(str(tmp_path / "from-cli")) == (tmp_path / "from-cli").resolve()
    assert resolve_data_dir(None) == (tmp_path / "from-env").resolve()
    monkeypatch.delenv(ENV_DATA_DIR)
    assert resolve_data_dir(None) == default_data_dir()


# ── config bootstrap ─────────────────────────────────────────


def test_config_bootstrap_first_run_generates_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KR_WEB_PASSWORD", raising=False)
    config, generated = load_or_create(tmp_path)
    assert generated
    assert config["web_console"]["password"] == generated
    saved = json.loads((tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert saved["web_console"]["password"] == generated
    # 二次读取：不再生成、内容不变。
    config2, generated2 = load_or_create(tmp_path)
    assert generated2 is None
    assert config2["web_console"]["password"] == generated


def test_config_bootstrap_env_password_skips_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KR_WEB_PASSWORD", "env-pass")
    config, generated = load_or_create(tmp_path)
    assert generated is None
    assert config["web_console"]["password"] == ""


# ── MainLLMConfig ────────────────────────────────────────────


def test_main_llm_config_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MAIN_LLM_API_KEY, raising=False)
    empty = Config({}).get_main_llm_config()
    assert not empty.configured

    cfg = Config(
        {
            "main_llm": {
                "base_url": "http://localhost:1234/v1",
                "model": "qwen3",
                "api_key": "from-config",
                "timeout_seconds": 60,
            }
        }
    ).get_main_llm_config()
    assert cfg.configured
    assert cfg.api_key == "from-config"
    assert cfg.timeout_seconds == 60

    monkeypatch.setenv(ENV_MAIN_LLM_API_KEY, "from-env")
    cfg_env = Config({"main_llm": {"api_key": "from-config"}}).get_main_llm_config()
    assert cfg_env.api_key == "from-env"


def test_main_llm_masked_in_public_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MAIN_LLM_API_KEY, raising=False)
    public = Config({"main_llm": {"api_key": "secret123"}}).to_public_dict()
    assert public["main_llm"]["api_key"] == "se****23"


# ── 组合根注入 ────────────────────────────────────────────────


class _SentinelLLM:
    async def generate(self, prompt: str, system_prompt: str = "", **kwargs: object) -> str:
        return "sentinel"


async def test_initializer_accepts_shell_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ka_core.plugin_initializer._module_available", lambda name: False
    )
    kb = InMemoryKnowledgeBaseReader()
    llm = _SentinelLLM()
    initializer = PluginInitializer(
        None,
        {"web_console": {"enabled": False}},
        tmp_path,
        llm_adapter=llm,
        kb_reader=kb,
    )
    await initializer.initialize()
    try:
        assert initializer.kb_reader is kb
        assert initializer.api is not None
        assert initializer.api._llm_adapter is llm
    finally:
        await initializer.teardown()


async def test_initializer_defaults_unchanged_without_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ka_core.plugin_initializer._module_available", lambda name: False
    )
    initializer = PluginInitializer(None, {"web_console": {"enabled": False}}, tmp_path)
    await initializer.initialize()
    try:
        assert type(initializer.kb_reader).__name__ == "AstrBotKnowledgeBaseReader"
    finally:
        await initializer.teardown()


# ── bootstrap 装配 ────────────────────────────────────────────


def test_resolve_main_llm_adapter_unconfigured(tmp_path: Path) -> None:
    assert resolve_main_llm_adapter(tmp_path, {}) is None


def test_resolve_main_llm_adapter_configured(tmp_path: Path) -> None:
    adapter = resolve_main_llm_adapter(
        tmp_path,
        {"main_llm": {"base_url": "http://localhost:1234/v1", "model": "qwen3"}},
    )
    assert type(adapter).__name__ == "LMStudioLLMAdapter"


def test_build_initializer_injects_memory_kb(tmp_path: Path) -> None:
    initializer = build_initializer(tmp_path, {"web_console": {"enabled": False}})
    assert isinstance(initializer._kb_reader_override, InMemoryKnowledgeBaseReader)
    assert initializer._llm_adapter_override is None
