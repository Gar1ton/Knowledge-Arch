"""skill 首次配置命令的端到端测试：真实后端（agent token + 白名单）跑在后台线程，客户端同步调用。"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest
from aiohttp import web

from ka_server.agent_access import CONNECTION_FILE_NAME, AgentAccess
from tests.backend.test_knowledge_arch_skill_client import CLIENT, CLIENT_PATH
from tests.backend.test_settings_api import _api
from web.server import build_app

SETUP = sys.modules["knowledge_arch_setup"]
UI_TOKEN = "ui-token"


@contextmanager
def _live_backend(tmp_path: Path) -> Iterator[tuple[str, AgentAccess, Any]]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict[str, Any] = {}

    async def start() -> None:
        api, _settings, _compat = await _api(tmp_path)
        access = AgentAccess(tmp_path / CONNECTION_FILE_NAME)
        app = build_app(api=api, upload_dir=tmp_path, token=UI_TOKEN, agent_access=access,
                        app_version="9.9.9")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        access.start(port, enabled=True)
        holder.update(api=api, access=access, runner=runner, url=f"http://127.0.0.1:{port}")
        ready.set()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.create_task(start())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        yield holder["url"], holder["access"], holder["api"]
    finally:
        asyncio.run_coroutine_threadsafe(holder["runner"].cleanup(), loop).result(10)
        holder["access"].remove()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


def _ui(url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        f"{url}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {UI_TOKEN}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_setup_flow_end_to_end(tmp_path: Path) -> None:
    with _live_backend(tmp_path) as (url, access, api):
        client, _source = CLIENT.resolve_connection_client(access.connection_file)

        status = SETUP.setup_status(client)
        assert status["status"] == "incomplete" and status["providers"] == []

        preview = SETUP.provider_add(client, preset="deepseek", label=None, base_url=None,
                                     kind=None, provider_id="ds", apply=False)
        assert preview["status"] == "preview" and api.list_providers()["providers"] == []
        added = SETUP.provider_add(client, preset="deepseek", label=None, base_url=None,
                                   kind=None, provider_id="ds", apply=True)
        assert added["provider"]["id"] == "ds" and added["needs_key"] is True

        ref = {"provider_id": "ds", "model": "deepseek-chat"}
        assert SETUP.settings_set(client, "flow.models.chat", ref, apply=False)["to"] == ref
        assert SETUP.setup_status(client)["models"]["flow.models.chat"] is None
        SETUP.settings_set(client, "flow.models.chat", ref, apply=True)
        assert SETUP.setup_status(client)["models"]["flow.models.chat"] == ref
        with pytest.raises(ValueError, match="request-secret"):
            SETUP.settings_set(client, "flow.notion.token", "x", apply=True)

        asked = SETUP.request_secret(client, "provider:ds", "chat model")
        assert asked["status"] == "pending"
        pending = SETUP.setup_status(client)["pending_secret_requests"]
        assert pending == [{"id": asked["request_id"], "label": "DeepSeek API key"}]
        _ui(url, f"/api/agent/secret-requests/{asked['request_id']}/fulfill", {"value": "sk-x"})
        done = SETUP.secret_status(client, asked["request_id"], wait=True, max_wait_seconds=5)
        assert done["status"] == "fulfilled"
        assert SETUP.providers_list(client)["providers"][0]["api_key_set"] is True


def test_cli_with_the_prompt_command_shape(tmp_path: Path) -> None:
    """与 prompt 里的命令一致：-I + --connection-file + 子命令。"""
    with _live_backend(tmp_path) as (_url, access, _api_obj):
        result = subprocess.run(
            [sys.executable, "-I", str(CLIENT_PATH), "--connection-file",
             str(access.connection_file), "setup-status"],
            check=False, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        blocked = subprocess.run(
            [sys.executable, "-I", str(CLIENT_PATH), "--connection-file",
             str(access.connection_file), "settings-set", "flow.r2.access_key_id", "x"],
            check=False, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "incomplete"
    assert blocked.returncode == CLIENT.UsageError.exit_code
    assert "request-secret" in blocked.stderr


def test_detect_finds_zotero_and_reports_missing_servers(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "home" / "Zotero").mkdir(parents=True)
    (tmp_path / "home" / "Zotero" / "zotero.sqlite").write_bytes(b"")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(SETUP, "LOCAL_SERVICES", {"ollama": "http://127.0.0.1:9/v1/models"})
    with _live_backend(tmp_path) as (_url, access, _api_obj):
        client, _source = CLIENT.resolve_connection_client(access.connection_file)
        found = SETUP.detect(client)
    assert found["zotero_data_dirs"] == [str(tmp_path / "home" / "Zotero")]
    assert found["local_model_servers"]["ollama"] == {"running": False}


def test_provider_add_needs_a_preset_or_address(tmp_path: Path) -> None:
    with _live_backend(tmp_path) as (_url, access, _api_obj):
        client, _source = CLIENT.resolve_connection_client(access.connection_file)
        with pytest.raises(ValueError, match="Unknown preset"):
            SETUP.provider_add(client, preset="nope", label=None, base_url=None, kind=None,
                               provider_id=None, apply=False)
        with pytest.raises(ValueError, match="--label"):
            SETUP.provider_add(client, preset=None, label="x", base_url=None, kind=None,
                               provider_id=None, apply=False)
