"""AI 助手接入：连接文件、agent token 白名单、机密拦截、密钥请求、prompt 与 skill 一键安装。"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ka_server.agent_access import CONNECTION_FILE_NAME, AgentAccess, connection_file_for
from kacore import agent_skill
from tests.backend.test_settings_api import _api
from web.agent_routes import AGENT_ROUTES
from web.server import build_app

UI_TOKEN = "ui-token"
AGENT_TOKEN = "agent-token"
UI = {"Authorization": f"Bearer {UI_TOKEN}"}
AGENT = {"Authorization": f"Bearer {AGENT_TOKEN}"}


# ── 连接文件 ──────────────────────────────────────────────────────────


def test_connection_file_is_private_and_follows_the_switch(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / CONNECTION_FILE_NAME
    access = AgentAccess(path, token=AGENT_TOKEN)
    access.start(40123, enabled=False)
    assert not path.exists()

    access.set_enabled(True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["url"] == "http://127.0.0.1:40123" and data["token"] == AGENT_TOKEN
    assert data["pid"] == os.getpid() and data["version"] == 1
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert access.accepts(AGENT_TOKEN) and not access.accepts("other")

    access.set_enabled(False)
    assert not path.exists() and not access.accepts(AGENT_TOKEN)


def test_connection_file_location(tmp_path: Path) -> None:
    assert connection_file_for(str(tmp_path), tmp_path) == tmp_path / CONNECTION_FILE_NAME
    default = connection_file_for(None, tmp_path)
    assert default.name == CONNECTION_FILE_NAME and default.parent.name == "Knowledge Arch"


def test_status_reports_recent_agent_requests(tmp_path: Path) -> None:
    access = AgentAccess(tmp_path / CONNECTION_FILE_NAME, token=AGENT_TOKEN)
    access.start(1, enabled=True)
    assert access.status()["connected"] is False
    access.touch()
    assert access.status()["connected"] is True


# ── HTTP：白名单与机密 ────────────────────────────────────────────────


async def _clients(
    tmp_path: Path, *, enabled: bool = True
) -> tuple[TestClient, AgentAccess, object]:
    api, settings, _compat = await _api(tmp_path)
    access = AgentAccess(tmp_path / "agent" / CONNECTION_FILE_NAME, token=AGENT_TOKEN)
    access.start(40123, enabled=enabled)
    app = build_app(
        api=api, upload_dir=tmp_path, token=UI_TOKEN, agent_access=access, app_version="9.9.9"
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, access, api


async def test_agent_token_reaches_only_allowlisted_routes(tmp_path: Path) -> None:
    client, _access, api = await _clients(tmp_path)
    try:
        assert (await client.get("/api/settings/schema", headers=AGENT)).status == 200
        for method, path in (
            ("POST", "/api/shutdown"),
            ("POST", "/api/restore"),
            ("GET", "/api/fs/browse"),
            ("DELETE", "/api/providers/x"),
            ("GET", "/api/agent/prompt"),
            ("POST", "/api/agent/skill/install"),
        ):
            resp = await client.request(method, path, headers=AGENT)
            assert resp.status == 403, (method, path)
        assert (await client.get("/api/settings/schema", headers=UI)).status == 200
        bad = {"Authorization": "Bearer nope"}
        assert (await client.get("/api/settings/schema", headers=bad)).status == 401
    finally:
        await client.close()


async def test_disabled_access_rejects_the_agent_token(tmp_path: Path) -> None:
    client, _access, _api_obj = await _clients(tmp_path, enabled=False)
    try:
        assert (await client.get("/api/settings/schema", headers=AGENT)).status == 401
    finally:
        await client.close()


async def test_agent_cannot_write_secrets(tmp_path: Path) -> None:
    client, _access, api = await _clients(tmp_path)
    try:
        resp = await client.patch(
            "/api/settings", json={"key": "flow.notion.token", "value": "secret"}, headers=AGENT
        )
        assert resp.status == 403 and (await resp.json())["code"] == "secret_requires_user"
        ok = await client.patch(
            "/api/settings", json={"key": "flow.graph.query_mode", "value": "local"}, headers=AGENT
        )
        assert ok.status == 200
        with_key = {"label": "DS", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-1"}
        assert (await client.post("/api/providers", json=with_key, headers=AGENT)).status == 403
        no_key = {"label": "DS", "base_url": "https://api.deepseek.com/v1", "id": "ds"}
        assert (await client.post("/api/providers", json=no_key, headers=AGENT)).status == 201
        patch = await client.patch("/api/providers/ds", json={"api_key": "sk-2"}, headers=AGENT)
        assert patch.status == 403
        assert api.list_providers()["providers"][0]["api_key_set"] is False
    finally:
        await client.close()


async def test_settings_switch_writes_and_removes_the_connection_file(tmp_path: Path) -> None:
    client, access, _api_obj = await _clients(tmp_path, enabled=False)
    try:
        on = {"key": "prefs.general.agent_access", "value": True}
        assert (await client.patch("/api/settings", json=on, headers=UI)).status == 200
        assert access.connection_file.is_file()
        off = {"key": "prefs.general.agent_access", "value": False}
        assert (await client.patch("/api/settings", json=off, headers=UI)).status == 200
        assert not access.connection_file.exists()
    finally:
        await client.close()


async def test_secret_request_round_trip_never_exposes_the_value(tmp_path: Path) -> None:
    client, _access, api = await _clients(tmp_path)
    try:
        api.create_provider({"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                             "id": "ds"})
        created = await client.post(
            "/api/agent/secret-requests",
            json={"target": "provider:ds", "reason": "chat model"}, headers=AGENT,
        )
        assert created.status == 201
        item = await created.json()
        assert item["status"] == "pending" and item["label"] == "DeepSeek API key"
        again = await client.post(
            "/api/agent/secret-requests", json={"target": "provider:ds"}, headers=AGENT
        )
        assert (await again.json())["id"] == item["id"]

        fulfill = f"/api/agent/secret-requests/{item['id']}/fulfill"
        assert (await client.post(fulfill, json={"value": "sk-x"}, headers=AGENT)).status == 403
        done = await client.post(fulfill, json={"value": "sk-x"}, headers=UI)
        assert done.status == 200 and (await done.json())["status"] == "fulfilled"
        assert api.list_providers()["providers"][0]["api_key_set"] is True

        seen = await client.get(f"/api/agent/secret-requests/{item['id']}", headers=AGENT)
        assert "sk-x" not in await seen.text()
        listing = await client.get("/api/agent/secret-requests", headers=AGENT)
        assert "sk-x" not in await listing.text()
    finally:
        await client.close()


async def test_secret_request_targets_are_validated(tmp_path: Path) -> None:
    client, _access, _api_obj = await _clients(tmp_path)
    try:
        post = client.post
        ok = await post("/api/agent/secret-requests", json={"target": "setting:flow.notion.token"},
                        headers=AGENT)
        assert ok.status == 201
        not_secret = await post(
            "/api/agent/secret-requests", json={"target": "setting:flow.graph.query_mode"},
            headers=AGENT,
        )
        assert not_secret.status == 400
        ghost = await post("/api/agent/secret-requests", json={"target": "provider:ghost"},
                           headers=AGENT)
        assert ghost.status == 404
        cancel = await post(f"/api/agent/secret-requests/{(await ok.json())['id']}/cancel",
                            headers=AGENT)
        assert (await cancel.json())["status"] == "cancelled"
    finally:
        await client.close()


async def test_status_shows_connection_and_skill_state_to_the_ui(tmp_path: Path,
                                                                 monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    client, _access, _api_obj = await _clients(tmp_path)
    try:
        agent_view = await (await client.get("/api/agent/status", headers=AGENT)).json()
        assert agent_view["enabled"] is True and "skill" not in agent_view
        ui_view = await (await client.get("/api/agent/status", headers=UI)).json()
        assert ui_view["connected"] is True  # 上面的 agent 请求刚刚来过
        assert ui_view["skill"]["codex"]["state"] == "missing"
    finally:
        await client.close()


async def test_prompt_and_one_click_install(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    client, access, _api_obj = await _clients(tmp_path)
    try:
        zh = await (await client.get("/api/agent/prompt?lang=zh", headers=UI)).json()
        assert "请用中文和我交流" in zh["prompt"] and "setup-status" in zh["prompt"]
        assert access.connection_file.name in zh["prompt"]
        en = await (await client.get("/api/agent/prompt?lang=en", headers=UI)).json()
        assert "Reply to me in English" in en["prompt"] and "{command}" not in en["prompt"]

        resp = await client.post(
            "/api/agent/skill/install", json={"targets": ["codex", "claude"]}, headers=UI
        )
        assert resp.status == 200
        installed = tmp_path / "codex" / "skills" / agent_skill.SKILL_NAME
        skill_md = (installed / "SKILL.md").read_text(encoding="utf-8")
        assert str(installed / "scripts" / "knowledge_arch_client.py") in skill_md
        assert (tmp_path / "claude" / "skills" / agent_skill.SKILL_NAME / "SKILL.md").is_file()
        status = await (await client.get("/api/agent/status", headers=UI)).json()
        assert status["skill"]["codex"]["state"] == "current"
    finally:
        await client.close()


# ── skill 渲染与安装 ──────────────────────────────────────────────────


def test_install_skips_a_foreign_skill_with_the_same_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    foreign = tmp_path / "codex" / "skills" / agent_skill.SKILL_NAME
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("mine", encoding="utf-8")

    result = agent_skill.install_skill(
        ["codex"], version="1", connection_file=tmp_path / CONNECTION_FILE_NAME
    )

    assert result["codex"]["state"] == "foreign"
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert agent_skill.install_status("1")["codex"]["state"] == "foreign"


def test_reinstall_marks_outdated_then_current(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    conn = tmp_path / CONNECTION_FILE_NAME
    agent_skill.install_skill(["claude"], version="1", connection_file=conn)
    assert agent_skill.install_status("2")["claude"]["state"] == "outdated"
    agent_skill.install_skill(["claude"], version="2", connection_file=conn)
    assert agent_skill.install_status("2")["claude"]["state"] == "current"
    assert not list((tmp_path / "claude").rglob("__pycache__"))


def test_render_replaces_only_the_command_block() -> None:
    text = "intro\n<!-- ka:client-command -->\nold\n<!-- /ka:client-command -->\noutro\n"
    rendered = agent_skill.render_skill_md(text, "py client.py")
    assert "old" not in rendered and "py client.py <command>" in rendered
    assert rendered.startswith("intro\n") and rendered.endswith("outro\n")


def test_bundled_skill_has_the_command_block_and_prompts() -> None:
    source = agent_skill.skill_source_dir()
    assert "<!-- ka:client-command -->" in (source / "SKILL.md").read_text(encoding="utf-8")
    for lang in ("en", "zh"):
        template = (source / "assets" / f"setup-prompt.{lang}.md").read_text(encoding="utf-8")
        assert "{skill_md}" in template and "{command}" in template
    assert (source / "references" / "setup.md").is_file()


@pytest.mark.parametrize("method,path", sorted(AGENT_ROUTES))
def test_every_allowlisted_route_exists(method: str, path: str, tmp_path: Path) -> None:
    app = build_app(api=object(), upload_dir=tmp_path, token=UI_TOKEN)  # type: ignore[arg-type]
    routes = {
        (route.method, str(route.resource.canonical))  # type: ignore[union-attr]
        for route in app.router.routes()
    }
    assert (method, path) in routes


def test_startup_refresh_only_touches_our_installs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    old = tmp_path / "old" / CONNECTION_FILE_NAME
    agent_skill.install_skill(["codex"], version="1", connection_file=old)
    foreign = tmp_path / "claude" / "skills" / agent_skill.SKILL_NAME
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("mine", encoding="utf-8")

    new = tmp_path / "new" / CONNECTION_FILE_NAME
    assert agent_skill.refresh_installed(version="2", connection_file=new) == ["codex"]

    skill_md = tmp_path / "codex" / "skills" / agent_skill.SKILL_NAME / "SKILL.md"
    assert str(new) in skill_md.read_text(encoding="utf-8")
    assert agent_skill.install_status("2")["codex"]["state"] == "current"
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "mine"


def test_prompt_paths_under_home_hide_the_user_name(tmp_path: Path, monkeypatch) -> None:
    """prompt 会发给 AI 服务商：家目录下的路径写成 $HOME / ~，不带用户名。"""
    if os.name == "nt":
        pytest.skip("Windows keeps absolute paths (Git Bash expands $HOME differently)")
    home = tmp_path / "home" / "alice"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    skill = home / "skills" / agent_skill.SKILL_NAME
    command = agent_skill.client_command(skill, home / ".config" / CONNECTION_FILE_NAME,
                                         Path("/opt/Knowledge Arch/python/bin/python3"))
    assert "alice" not in command
    assert '"$HOME/skills/operate-knowledge-arch/scripts/knowledge_arch_client.py"' in command
    assert "'/opt/Knowledge Arch/python/bin/python3'" in command
    monkeypatch.setattr(agent_skill, "client_command", lambda *a, **k: command)
    prompt = agent_skill.build_prompt("en", skill_dir=skill, connection_file=home / "c.json")
    assert "alice" not in prompt and "~/skills/operate-knowledge-arch/SKILL.md" in prompt


def test_home_quoting_survives_a_real_shell(tmp_path: Path, monkeypatch) -> None:
    if os.name == "nt":
        pytest.skip("POSIX shells only")
    import subprocess

    home = tmp_path / "ho me$x"
    target = home / "a b" / "c.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    quoted = agent_skill._quote(target)
    out = subprocess.run(["sh", "-c", f"printf %s {quoted}"], capture_output=True, text=True,
                         env={"HOME": str(home), "PATH": os.environ.get("PATH", "")}, check=True)
    assert out.stdout == str(target)
