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


def test_copy_sources_writes_bundle_marker(tmp_path: Path) -> None:
    from ka_server.bytecode_cache import BUNDLE_MARKER

    assert bundle.BUNDLE_MARKER == BUNDLE_MARKER
    assert (bundle.copy_sources(tmp_path) / BUNDLE_MARKER).is_file()


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_prune_runtime_drops_bytecode_tests_headers_and_unused_pyarrow(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    sp = python_dir / "lib" / "python3.12" / "site-packages"
    keep = [
        _touch(python_dir / "include" / "python3.12" / "Python.h"),  # 解释器头文件留给源码编译
        _touch(python_dir / "lib" / "python3.12" / "os.py"),
        _touch(sp / "pandas" / "core" / "frame.py"),
        _touch(sp / "numpy" / "testing" / "__init__.py"),  # testing ≠ tests，运行期会被导入
        _touch(sp / "pyarrow" / "lib.cpython-312-x86_64-linux-gnu.so"),
        _touch(sp / "pyarrow" / "libarrow_substrait.so.2600"),  # 核心 lib 直接链接，不能删
        _touch(sp / "pyarrow" / "parquet" / "core.py"),
        _touch(sp / "pyarrow" / "fs.py"),
    ]
    drop = [
        _touch(python_dir / "lib" / "python3.12" / "__pycache__" / "os.cpython-312.pyc"),
        _touch(sp / "legacy_module.pyc"),
        _touch(sp / "pandas" / "tests" / "frame" / "test_api.py"),
        _touch(sp / "pyarrow" / "include" / "arrow" / "api.h"),
        _touch(sp / "pyarrow" / "libarrow_flight.so.2600"),
        _touch(sp / "pyarrow" / "_flight.cpython-312-x86_64-linux-gnu.so"),
        _touch(sp / "pyarrow" / "flight.py"),
        _touch(sp / "pyarrow" / "libarrow_s3.so.2600"),
        _touch(sp / "pyarrow" / "_s3fs.cpython-312-x86_64-linux-gnu.so"),
        _touch(sp / "pyarrow" / "arrow_flight.dll"),  # Windows 命名
        _touch(sp / "pyarrow" / "_pyarrow_cpp_tests.cpython-312-x86_64-linux-gnu.so"),
    ]

    freed = bundle.prune_runtime(python_dir, sp)

    assert freed == 10 * len(drop)
    assert all(p.exists() for p in keep), [p for p in keep if not p.exists()]
    assert not any(p.exists() for p in drop), [p for p in drop if p.exists()]
