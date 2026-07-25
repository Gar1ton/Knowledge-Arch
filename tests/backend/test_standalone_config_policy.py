"""standalone_writable 策略门控：插件模式 4 面板节保持只读，独立壳可编辑。"""
from __future__ import annotations

from pathlib import Path

import pytest

from kacore.config import standalone_writable_keys
from kacore.plugin_initializer import PluginInitializer


async def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str):
    monkeypatch.setattr("kacore.plugin_initializer._module_available", lambda name: False)
    initializer = PluginInitializer(
        None, {"web_console": {"enabled": False}}, tmp_path, shell=shell
    )
    await initializer.initialize()
    return initializer


def test_standalone_writable_registry_covers_panel_sections() -> None:
    keys = standalone_writable_keys()
    assert "host" in keys["web_console"] and "port" in keys["web_console"]
    assert "account_id" in keys["r2_sync"] and "bucket" in keys["r2_sync"]
    assert "parent_page_id" in keys["notion_sync"]
    # 真机密绝不放行。
    assert "secret_access_key" not in keys.get("r2_sync", frozenset())


async def test_plugin_shell_panel_sections_stay_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, "plugin")
    try:
        api = initializer.api
        assert api.shell == "plugin"
        with pytest.raises(ValueError):
            await api.update_config_value("web_console", "host", "0.0.0.0")
        with pytest.raises(ValueError):
            await api.update_config_value("r2_sync", "account_id", "acc")
        with pytest.raises(ValueError):
            await api.update_config_value("r2_sync", "access_key_id", "ak")
    finally:
        await initializer.teardown()


async def test_standalone_shell_panel_sections_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = await _build(tmp_path, monkeypatch, "standalone")
    try:
        api = initializer.api
        assert api.shell == "standalone"
        result = await api.update_config_value("web_console", "host", "0.0.0.0")
        assert result["status"] == "success" and result["restart_required"]
        result = await api.update_config_value("r2_sync", "account_id", "acc")
        assert result["status"] == "success"
        # ID 类半机密放行；真机密仍被拒。
        result = await api.update_config_value("r2_sync", "access_key_id", "ak-123")
        assert result["status"] == "success"
        with pytest.raises(ValueError):  # 真机密未登记 standalone_writable，直接不在放行集
            await api.update_config_value("r2_sync", "secret_access_key", "s3cret")
        # capabilities 暴露壳标识；standalone 恒装配对话 Agent（主模型未配置也装配，
        # llm_ready 单独表达就绪度）。
        caps = await api.get_capabilities()
        assert caps["shell"] == "standalone"
        assert caps["conversation_available"] is True
        assert api.conversation_info() == {"available": True, "llm_ready": False}
    finally:
        await initializer.teardown()
