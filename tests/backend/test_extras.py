"""可选组件（extras）契约测试：事务性安装、回滚、激活、版本过期、uv 命令与 HTTP 接口。

安装器用内存替身（FakeExtrasInstaller）或一个假 uv 脚本，不联网、不真的装包。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from kacore.api import KnowledgeRepositoryApi
from kacore.extras.base import ExtrasUnavailableError
from kacore.extras.manager import (
    MARKER_FILE,
    STATE_INSTALLED,
    STATE_MISSING,
    STATE_STALE,
    ExtrasManager,
    python_tag,
)
from kacore.extras.memory import FakeExtrasInstaller
from kacore.extras.uv import UvInstaller, locate_uv
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from web.server import build_app

R2_SPEC = "boto3"


def _manager(tmp_path: Path, installer: FakeExtrasInstaller) -> tuple[ExtrasManager, list[str]]:
    added: list[str] = []
    return ExtrasManager(tmp_path / "extras", installer, add_site_dir=added.append), added


# ── 事务性安装 ──────────────────────────────────────────────────


async def test_install_moves_staging_into_pack_dir_and_activates(tmp_path: Path) -> None:
    installer = FakeExtrasInstaller(packages={R2_SPEC: "fake_boto3_pkg"})
    manager, added = _manager(tmp_path, installer)
    lines: list[str] = []

    result = await manager.install("r2", on_line=lines.append)

    assert result["status"] == "ok" and result["restart_required"] is True
    pack = tmp_path / "extras" / "r2"
    assert (pack / "fake_boto3_pkg" / "__init__.py").is_file()
    marker = json.loads((pack / MARKER_FILE).read_text())
    assert marker["python"] == python_tag() and marker["specs"] == [R2_SPEC]
    assert manager.state("r2") == STATE_INSTALLED
    assert added == [str(pack)]
    assert lines == [f"installed {R2_SPEC}"]
    assert not list((tmp_path / "extras").glob(".r2.*"))
    # 安装器收到的目标是临时目录，不是最终目录。
    assert installer.calls[0]["target"] != pack


async def test_failed_install_rolls_back_and_keeps_previous_version(tmp_path: Path) -> None:
    installer = FakeExtrasInstaller(packages={R2_SPEC: "v1_pkg"})
    manager, _ = _manager(tmp_path, installer)
    assert (await manager.install("r2"))["status"] == "ok"

    installer.packages = {R2_SPEC: "v2_pkg"}
    installer.returncode = 2
    result = await manager.install("r2")

    assert result["status"] == "error" and result["returncode"] == 2
    assert result["restart_required"] is False
    pack = tmp_path / "extras" / "r2"
    assert (pack / "v1_pkg").is_dir() and not (pack / "v2_pkg").exists()
    assert not (tmp_path / "extras" / ".r2.partial").exists()


async def test_failed_first_install_leaves_nothing_behind(tmp_path: Path) -> None:
    manager, added = _manager(tmp_path, FakeExtrasInstaller(returncode=1))
    assert (await manager.install("lightrag"))["status"] == "error"
    assert manager.state("lightrag") == STATE_MISSING
    assert list((tmp_path / "extras").iterdir()) == []
    assert added == []


async def test_unavailable_installer_reports_error(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, FakeExtrasInstaller(is_available=False))
    result = await manager.install("r2")
    assert result["status"] == "error" and "unavailable" in result["message"]
    assert manager.state("r2") == STATE_MISSING


async def test_install_rejects_packages_outside_allowlist(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, FakeExtrasInstaller())
    with pytest.raises(ValueError):
        await manager.install("evil; rm -rf /")


async def test_local_models_pack_brings_torch_and_backend_selection(tmp_path: Path) -> None:
    installer = FakeExtrasInstaller()
    manager, _ = _manager(tmp_path, installer)
    await manager.install("local_embedding")
    call = installer.calls[0]
    assert "torch" in call["specs"]
    assert any(s.startswith("sentence-transformers") for s in call["specs"])
    assert call["extra_args"] == ["--torch-backend", "auto"]


# ── 激活与状态 ──────────────────────────────────────────────────


def _fake_pack(root: Path, key: str, python: str) -> Path:
    pack = root / key
    pack.mkdir(parents=True)
    (pack / MARKER_FILE).write_text(json.dumps({"key": key, "python": python}))
    return pack


def test_activate_skips_stale_packs_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "extras"
    good = _fake_pack(root, "r2", python_tag())
    _fake_pack(root, "lightrag", "2.7")
    added: list[str] = []
    manager = ExtrasManager(root, FakeExtrasInstaller(), add_site_dir=added.append)

    assert manager.activate() == ["r2"]
    assert manager.activate() == []
    assert added == [str(good)]
    assert manager.state("lightrag") == STATE_STALE
    status = {c["key"]: c for c in manager.status()["components"]}
    assert status["r2"]["active"] is True and status["lightrag"]["state"] == STATE_STALE
    assert "milvus" not in status  # 核心依赖随安装包提供，不在组件页
    assert set(status) == {"local_embedding", "lightrag", "r2"}


def test_activated_pack_is_importable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = tmp_path / "extras"
    pack = _fake_pack(root, "r2", python_tag())
    (pack / "ka_extras_probe_mod").mkdir()
    (pack / "ka_extras_probe_mod" / "__init__.py").write_text("VALUE = 42\n")
    ExtrasManager(root, FakeExtrasInstaller()).activate()
    import importlib

    assert importlib.import_module("ka_extras_probe_mod").VALUE == 42
    assert sys.path.index(str(pack)) > sys.path.index(os.path.dirname(os.__file__))
    sys.modules.pop("ka_extras_probe_mod", None)


def test_uninstall_removes_pack(tmp_path: Path) -> None:
    root = tmp_path / "extras"
    _fake_pack(root, "r2", python_tag())
    manager = ExtrasManager(root, FakeExtrasInstaller())
    assert manager.uninstall("r2")["status"] == "ok"
    assert manager.state("r2") == STATE_MISSING
    assert manager.uninstall("r2")["restart_required"] is False


# ── uv 安装器 ───────────────────────────────────────────────────


def test_uv_command_shape(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text("")
    installer = UvInstaller(str(uv), python="/opt/py/bin/python3")
    cmd = installer.command(["boto3"], tmp_path / "t", extra_args=["--torch-backend", "auto"])
    assert cmd[:4] == [str(uv), "pip", "install", "--no-config"]
    assert cmd[cmd.index("--target") + 1] == str(tmp_path / "t")
    assert cmd[cmd.index("--python") + 1] == "/opt/py/bin/python3"
    assert cmd[-3:] == ["--torch-backend", "auto", "boto3"]


def test_locate_uv_prefers_explicit_then_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "my-uv"
    explicit.write_text("")
    env_uv = tmp_path / "env-uv"
    env_uv.write_text("")
    monkeypatch.setenv("KA_UV", str(env_uv))
    assert locate_uv(str(explicit)) == str(explicit)
    assert locate_uv(None) == str(env_uv)
    assert locate_uv(str(tmp_path / "missing")) is None


async def test_missing_uv_raises_unavailable(tmp_path: Path) -> None:
    installer = UvInstaller(None)
    assert installer.available is False
    with pytest.raises(ExtrasUnavailableError, match="uv"):
        await installer.install(["boto3"], tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="假 uv 用 POSIX shell 脚本")
async def test_uv_subprocess_streams_output_and_installs_into_target(tmp_path: Path) -> None:
    script = tmp_path / "uv"
    script.write_text(
        "#!/bin/sh\n"
        'while [ "$1" != "--target" ]; do shift; done\n'
        'mkdir -p "$2/fake_uv_pkg"\n'
        'echo "Resolved 1 package"\n'
        "echo 'Installed 1 package'\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    added: list[str] = []
    manager = ExtrasManager(
        tmp_path / "extras", UvInstaller(str(script)), add_site_dir=added.append
    )
    lines: list[str] = []
    result = await manager.install("r2", on_line=lines.append)
    assert result["status"] == "ok", result
    assert (tmp_path / "extras" / "r2" / "fake_uv_pkg").is_dir()
    assert lines == ["Resolved 1 package", "Installed 1 package"]


# ── API 与 HTTP ─────────────────────────────────────────────────


async def test_api_install_and_list_components(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, FakeExtrasInstaller())
    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(), extras_manager=manager
    )
    result = await api.install_dependency("r2")
    assert result["status"] == "ok"
    components = {c["key"]: c for c in api.list_components()["components"]}
    assert components["r2"]["state"] == STATE_INSTALLED
    assert api.list_components()["installer_available"] is True


async def test_api_without_extras_manager_reports_unsupported() -> None:
    api = KnowledgeRepositoryApi(source_store=InMemorySourceDocumentStore())
    result = await api.install_dependency("r2")
    assert result["status"] == "error" and result["restart_required"] is False
    assert api.list_components()["components"] == []


async def test_component_routes(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, FakeExtrasInstaller())
    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(), extras_manager=manager
    )
    app = build_app(api=api, upload_dir=tmp_path / "u", token="t")
    client = TestClient(TestServer(app), headers={"Authorization": "Bearer t"})
    await client.start_server()
    try:
        assert (await client.post("/api/components/evil/install")).status == 400
        resp = await client.post("/api/components/r2/install")
        assert resp.status == 200 and (await resp.json())["key"] == "r2"
        listing = await (await client.get("/api/components")).json()
        assert {c["key"]: c["state"] for c in listing["components"]}["r2"] == STATE_INSTALLED
        resp = await client.delete("/api/components/r2")
        assert resp.status == 200 and (await resp.json())["restart_required"] is True
    finally:
        await client.close()


def test_ka_server_activates_installed_packs_before_initialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ka_server.__main__ import open_extras
    from ka_server.paths import AppPaths

    monkeypatch.setattr(sys, "path", list(sys.path))
    paths = AppPaths.resolve(tmp_path / "data")
    pack = _fake_pack(paths.extras, "lightrag", python_tag())
    manager = open_extras(paths, str(tmp_path / "no-such-uv"))
    assert str(pack) in sys.path
    assert manager.status()["installer_available"] is False


async def test_forget_master_key_route(tmp_path: Path) -> None:
    from kacore.settings.memory import MemorySecretStore
    from kacore.settings.store import SettingsStore

    secrets = MemorySecretStore({"provider.x.api_key": "k"})
    api = KnowledgeRepositoryApi(
        source_store=InMemorySourceDocumentStore(),
        settings_store=SettingsStore.in_memory(secrets=secrets),
    )
    bare = KnowledgeRepositoryApi(source_store=InMemorySourceDocumentStore())
    for target, expected in ((api, 200), (bare, 501)):
        app = build_app(api=target, upload_dir=tmp_path / "u", token="t")
        client = TestClient(TestServer(app), headers={"Authorization": "Bearer t"})
        await client.start_server()
        try:
            resp = await client.post("/api/secrets/forget-master-key")
            assert resp.status == expected
        finally:
            await client.close()
    assert not secrets.has_secret("provider.x.api_key")
