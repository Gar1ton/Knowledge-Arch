"""stdio Notion 传输与 CLI 子命令契约测试（session 经工厂注入，不 spawn 真进程）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from standalone.cli import main as cli_main
from standalone.notion_transport import StdioNotionToolCaller


class _FakeSession:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        self.calls.append((tool_name, arguments))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("boom")
        return {"id": "page-1", "tool": tool_name}


async def test_tool_caller_passthrough() -> None:
    session = _FakeSession()

    async def factory() -> _FakeSession:
        return session

    caller = StdioNotionToolCaller("token", session_factory=factory)
    result = await caller("notion_query_database", {"database_id": "db"})
    assert result == {"id": "page-1", "tool": "notion_query_database"}
    assert session.calls[0][1]["database_id"] == "db"


async def test_tool_caller_reconnects_once_then_succeeds() -> None:
    sessions: list[_FakeSession] = []

    async def factory() -> _FakeSession:
        # 第一个会话首调失败 → 传输层应弃用它并重建。
        session = _FakeSession(fail_times=1 if not sessions else 0)
        sessions.append(session)
        return session

    caller = StdioNotionToolCaller("token", session_factory=factory)
    result = await caller("notion_retrieve_database", {"database_id": "db"})
    assert result["id"] == "page-1"
    assert len(sessions) == 2  # 重连语义：恰好重建一次


async def test_tool_caller_raises_after_two_failures() -> None:
    async def factory() -> _FakeSession:
        return _FakeSession(fail_times=99)

    caller = StdioNotionToolCaller("token", session_factory=factory)
    with pytest.raises(RuntimeError, match="重连重试后仍失败"):
        await caller("notion_delete_block", {"block_id": "b"})


async def test_tool_caller_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KR_NOTION_API_TOKEN", raising=False)
    caller = StdioNotionToolCaller()
    with pytest.raises(RuntimeError, match="KR_NOTION_API_TOKEN"):
        await caller("notion_query_database", {})


# ── CLI 子命令 ────────────────────────────────────────────────


def test_cli_passwd_updates_config(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config_path = tmp_path / "standalone_config.json"
    config_path.write_text(
        json.dumps({"web_console": {"password": "old"}}), encoding="utf-8"
    )
    assert cli_main(["passwd", "--data-dir", str(tmp_path), "--set", "new-pass"]) == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["web_console"]["password"] == "new-pass"


def test_cli_passwd_missing_config(tmp_path: Path) -> None:
    assert cli_main(["passwd", "--data-dir", str(tmp_path), "--set", "x"]) == 1


def test_cli_status_runs(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli_main(["status", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "数据目录" in out and "主 LLM" in out


def test_cli_doctor_runs(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli_main(["doctor", "--data-dir", str(tmp_path)]) == 0
    assert "体检" in capsys.readouterr().out
