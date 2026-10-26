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


def _load_updater_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "updater_add_appimage", ROOT / "desktop" / "scripts" / "updater_add_appimage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_updater_manifest_gains_the_appimage_and_keeps_other_platforms(tmp_path) -> None:
    script = _load_updater_script()
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({
        "version": "2.0.0",
        "platforms": {
            "darwin-aarch64": {"signature": "mac", "url": "https://x/mac.tar.gz"},
            "linux-x86_64-deb": {"signature": "deb", "url": "https://x/a.deb"},
        },
    }), encoding="utf-8")
    sig = tmp_path / "a.AppImage.sig"
    sig.write_text("appimage-sig\n", encoding="utf-8")

    args = ["--latest", str(latest), "--sig", str(sig), "--url", "https://x/a.AppImage"]
    assert script.main(args) == 0

    platforms = json.loads(latest.read_text(encoding="utf-8"))["platforms"]
    appimage = {"signature": "appimage-sig", "url": "https://x/a.AppImage"}
    assert platforms["linux-x86_64-appimage"] == appimage
    assert platforms["linux-x86_64"] == appimage  # 原本没有通用键：补上
    assert platforms["darwin-aarch64"]["signature"] == "mac"
    assert platforms["linux-x86_64-deb"]["url"] == "https://x/a.deb"


def test_updater_manifest_rejects_an_empty_signature() -> None:
    import pytest

    with pytest.raises(ValueError, match="signature"):
        _load_updater_script().add_appimage({"platforms": {}}, "  ", "https://x/a.AppImage")


def test_updater_manifest_keeps_an_existing_generic_linux_entry() -> None:
    latest = {"platforms": {"linux-x86_64": {"signature": "deb", "url": "https://x/a.deb"}}}
    _load_updater_script().add_appimage(latest, "sig", "https://x/a.AppImage")
    assert latest["platforms"]["linux-x86_64"]["url"] == "https://x/a.deb"
    assert latest["platforms"]["linux-x86_64-appimage"]["url"] == "https://x/a.AppImage"


def test_platform_window_configs_only_change_the_title_bar() -> None:
    """平台配置按 JSON Merge Patch 整体替换 windows 数组：除标题栏字段外必须与基础配置一致。"""
    tauri = ROOT / "desktop" / "src-tauri"
    base = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))["app"]["windows"][0]
    title_bar_keys = {
        "windows": {"decorations", "shadow"},
        "linux": {"decorations"},
        "macos": {"titleBarStyle", "hiddenTitle", "trafficLightPosition"},
    }
    for platform, extra in title_bar_keys.items():
        conf = json.loads((tauri / f"tauri.{platform}.conf.json").read_text(encoding="utf-8"))
        (window,) = conf["app"]["windows"]
        assert set(window) - set(base) == extra, platform
        assert {k: v for k, v in window.items() if k not in extra} == base, platform
