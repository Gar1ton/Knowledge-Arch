"""ka_server 进程薄壳：目录解析、数据目录独占锁、退出信号、就绪协议与端到端启动链。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ka_server import __main__ as entry
from ka_server.lifecycle import (
    DataDirLock,
    DataDirLockedError,
    announce_ready,
    pid_alive,
    wait_for_shutdown,
)
from ka_server.paths import AppPaths

ROOT = Path(__file__).resolve().parents[2]

# ── 目录 ─────────────────────────────────────────────────────────


def test_explicit_data_dir_holds_data_and_logs(tmp_path: Path) -> None:
    paths = AppPaths.resolve(tmp_path / "ka")
    assert paths.data == (tmp_path / "ka").resolve()
    assert paths.logs == paths.data / "logs"
    assert paths.settings_file == paths.data / "settings.json"
    assert paths.secrets == paths.data / "secrets"
    paths.ensure()
    assert paths.logs.is_dir() and paths.uploads.is_dir()


def test_default_dirs_come_from_platformdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, object]] = []

    def fake_data(name: str, appauthor: object = None) -> str:
        seen.append((name, appauthor))
        return str(tmp_path / "data")

    monkeypatch.setattr("ka_server.paths.platformdirs.user_data_dir", fake_data)
    monkeypatch.setattr(
        "ka_server.paths.platformdirs.user_log_dir", lambda *a, **k: str(tmp_path / "log")
    )
    paths = AppPaths.resolve(None)
    assert paths.data == tmp_path / "data" and paths.logs == tmp_path / "log"
    assert seen == [("Knowledge Arch", False)]


# ── 数据目录锁 ───────────────────────────────────────────────────


def test_data_dir_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    lock_file = tmp_path / ".ka_server.lock"
    first = DataDirLock(lock_file)
    first.acquire()
    try:
        with pytest.raises(DataDirLockedError, match="another Knowledge Arch backend"):
            DataDirLock(lock_file).acquire()
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        first.release()
    with DataDirLock(lock_file):
        pass
    assert lock_file.exists()  # 锁是 OS 级的，文件留着不代表被持有


# ── 退出信号 ─────────────────────────────────────────────────────


def test_pid_alive_reports_running_and_exited_processes() -> None:
    assert pid_alive(os.getpid())
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not pid_alive(proc.pid)
    assert not pid_alive(0)


async def test_wait_for_shutdown_returns_on_request() -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, stop.set)
    assert await wait_for_shutdown(stop) == "requested"


async def test_wait_for_shutdown_returns_when_parent_exits(monkeypatch) -> None:
    monkeypatch.setattr("ka_server.lifecycle._PARENT_POLL_SECONDS", 0.05)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert await wait_for_shutdown(asyncio.Event(), parent_pid=proc.pid) == "parent_exited"


def test_ready_line_carries_only_the_port() -> None:
    out = io.StringIO()
    announce_ready(41234, out)
    assert out.getvalue() == "KA_READY port=41234\n"


def test_missing_token_exits_with_usage_error(monkeypatch, capsys) -> None:
    monkeypatch.delenv(entry.ENV_TOKEN, raising=False)
    assert entry.main(["serve", "--port", "0"]) == entry.EXIT_USAGE
    assert "KA_TOKEN" in capsys.readouterr().err


def test_dev_origins_merge_cli_and_environment(monkeypatch) -> None:
    monkeypatch.setenv(entry.ENV_DEV_ORIGINS, "http://localhost:3000, ,http://127.0.0.1:3000")
    assert entry.dev_origins(["http://a"]) == [
        "http://a",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


# ── 端到端：真实子进程 ───────────────────────────────────────────


def _spawn(data_dir: Path, token: str, *extra: str) -> subprocess.Popen[str]:
    env = {**os.environ, entry.ENV_TOKEN: token, "PYTHONPATH": str(ROOT)}
    env.pop(entry.ENV_DEV_ORIGINS, None)
    # stderr 落到文件：只读 stdout 时若 stderr 用管道，缓冲写满会让后端阻塞。
    stderr = open(data_dir.parent / f"stderr-{token}.log", "w", encoding="utf-8")  # noqa: SIM115
    return subprocess.Popen(
        [sys.executable, "-m", "ka_server", "serve", "--port", "0", "--data-dir", str(data_dir),
         *extra],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
    )


def _stderr(data_dir: Path, token: str) -> str:
    return (data_dir.parent / f"stderr-{token}.log").read_text(encoding="utf-8")


def _read_port(proc: subprocess.Popen[str], timeout: float = 90.0) -> int:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("KA_READY port="):
            return int(line.strip().split("=", 1)[1])
        if not line and proc.poll() is not None:
            break
    raise AssertionError(f"backend never became ready (exit={proc.poll()})")


def _request(port: int, path: str, token: str | None, method: str = "GET") -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _vector_free_settings(data_dir: Path) -> None:
    """端到端只验证进程链；跳过向量库避免加载本地模型。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "runtime_config.json").write_text(
        json.dumps({"vector_db": {"backend": "lexical"}}), encoding="utf-8"
    )


def test_serve_end_to_end_ready_auth_and_orderly_shutdown(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _vector_free_settings(data_dir)
    token = "e2e-token-must-not-leak"
    proc = _spawn(data_dir, token)
    try:
        port = _read_port(proc)
        assert _request(port, "/api/health", None)[0] == 401
        assert _request(port, "/api/health", token) == (200, {"status": "ok"})

        second = _spawn(data_dir, "other-token")
        assert second.wait(timeout=60) == entry.EXIT_DATA_DIR_LOCKED
        assert "DataDirLockedError" in _stderr(data_dir, "other-token")

        assert _request(port, "/api/shutdown", token, method="POST")[0] == 202
        assert proc.wait(timeout=60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    stdout = proc.stdout.read() if proc.stdout else ""
    stderr = _stderr(data_dir, token)
    log_text = (data_dir / "logs" / "ka_server.log").read_text(encoding="utf-8")
    assert "shutdown complete" in log_text
    for text in (stdout, stderr, log_text):
        assert token not in text


def test_serve_exits_when_parent_process_disappears(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _vector_free_settings(data_dir)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    proc = _spawn(data_dir, "parent-token", "--parent-pid", str(parent.pid))
    try:
        _read_port(proc)
        parent.kill()
        parent.wait()
        assert proc.wait(timeout=60) == 0
    finally:
        for p in (parent, proc):
            if p.poll() is None:
                p.kill()
    log_text = (data_dir / "logs" / "ka_server.log").read_text(encoding="utf-8")
    assert "shutdown: parent_exited" in log_text
