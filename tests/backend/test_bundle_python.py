"""桌面打包脚本的纯函数部分：源码清单、源码复制与 .pth 相对路径。"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "bundle_python", ROOT / "desktop" / "scripts" / "bundle_python.py"
)
assert _spec and _spec.loader
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)


def test_every_bundled_source_exists() -> None:
    for rel in bundle.SOURCES:
        assert (ROOT / rel).exists(), rel


def test_copy_sources_keeps_runtime_files_only(tmp_path: Path) -> None:
    ka = bundle.copy_sources(tmp_path)
    assert (ka / "ka_server" / "__main__.py").is_file()
    assert (ka / "kacore" / "app_initializer.py").is_file()
    assert (ka / "web" / "server.py").is_file() and (ka / "web" / "desktop_routes.py").is_file()
    assert sorted(p.name for p in (ka / "migrations").glob("*.sql"))[0].startswith("001_")
    assert not list(ka.rglob("__pycache__"))
    assert not (ka / "web" / "frontend").exists()


def test_pth_points_from_site_packages_to_ka(tmp_path: Path, monkeypatch) -> None:
    site = tmp_path / "python" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    ka = tmp_path / "ka"
    ka.mkdir()
    monkeypatch.setattr(bundle, "site_packages", lambda _python: site)
    pth = bundle.write_pth(tmp_path / "python" / "bin" / "python3", ka)
    rel = pth.read_text(encoding="utf-8").strip()
    assert not os.path.isabs(rel)
    assert (site / rel).resolve() == ka.resolve()
