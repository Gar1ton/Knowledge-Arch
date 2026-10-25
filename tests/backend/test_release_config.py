"""发布配置：configure_release 只消费已有的 CI 值；版本号在各处一致。"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "configure_release", ROOT / "desktop" / "scripts" / "configure_release.py"
)
assert _spec and _spec.loader
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def _conf() -> dict:
    return json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text())


def test_without_secrets_nothing_is_enabled() -> None:
    conf = _conf()
    outputs = release.configure(conf, {})
    assert outputs == {"features": "", "updater": "false", "windows_signing": "false"}
    assert "plugins" not in conf or "updater" not in conf["plugins"]
    assert not conf["bundle"].get("createUpdaterArtifacts")


def test_updater_and_windows_signing_from_env() -> None:
    conf = _conf()
    outputs = release.configure(
        conf,
        {
            "TAURI_UPDATER_PUBKEY": "PUBKEY",
            "GITHUB_REPOSITORY": "owner/repo",
            "WINDOWS_CERTIFICATE_THUMBPRINT": "ABC123",
        },
    )
    assert outputs["features"] == "--features updater"
    assert conf["plugins"]["updater"]["pubkey"] == "PUBKEY"
    assert conf["plugins"]["updater"]["endpoints"] == [
        "https://github.com/owner/repo/releases/latest/download/latest.json"
    ]
    assert conf["bundle"]["createUpdaterArtifacts"] is True
    assert conf["bundle"]["windows"]["certificateThumbprint"] == "ABC123"
    # 签名配置不能冲掉 NSIS 安装器配置。
    assert conf["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"


def test_versions_are_in_sync() -> None:
    from ka_server import APP_VERSION

    cargo = (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text()
    versions = {
        "ka_server": APP_VERSION,
        "tauri.conf.json": _conf()["version"],
        "Cargo.toml": re.search(r'^version = "([^"]+)"', cargo, re.M).group(1),  # type: ignore[union-attr]
        "desktop/package.json": json.loads((ROOT / "desktop" / "package.json").read_text())[
            "version"
        ],
        "web/frontend/package.json": json.loads(
            (ROOT / "web" / "frontend" / "package.json").read_text()
        )["version"],
    }
    assert len(set(versions.values())) == 1, versions
