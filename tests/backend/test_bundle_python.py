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
    for rel in bundle._TEST_ONLY:
        assert (ROOT / rel).is_file() and not (ka / rel).exists(), rel
    assert (ka / "kacore" / "settings" / "memory.py").is_file()


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


def test_slim_interpreter_keeps_one_real_python3(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bundle, "IS_WINDOWS", False)
    python_dir = tmp_path / "python"
    bin_dir = python_dir / "bin"
    real = _touch(bin_dir / "python3.12", 20)
    for alias in ("python", "python3"):
        (bin_dir / alias).symlink_to("python3.12")
    _touch(bin_dir / "keyring")  # 带构建机 shebang 的入口脚本
    stdlib = python_dir / "lib" / "python3.12"
    keep = [
        _touch(stdlib / "os.py"),
        _touch(stdlib / "venv" / "__init__.py"),
        _touch(stdlib / "lib-dynload" / "_ssl.cpython-312-x86_64-linux-gnu.so"),
        _touch(python_dir / "include" / "python3.12" / "Python.h"),
    ]
    drop = [
        _touch(python_dir / "lib" / "libpython3.12.so.1.0"),
        _touch(python_dir / "lib" / "libtcl9.0.so"),
        _touch(python_dir / "lib" / "tk9.0" / "tk.tcl"),
        _touch(stdlib / "tkinter" / "__init__.py"),
        _touch(stdlib / "idlelib" / "idle.py"),
        _touch(stdlib / "ensurepip" / "__init__.py"),
        _touch(stdlib / "config-3.12-x86_64-linux-gnu" / "Makefile"),
        _touch(stdlib / "lib-dynload" / "_tkinter.cpython-312-x86_64-linux-gnu.so"),
    ]

    bundle.slim_interpreter(python_dir)

    exe = bin_dir / "python3"
    assert sorted(p.name for p in bin_dir.iterdir()) == ["python3"]
    assert exe.is_file() and not exe.is_symlink() and exe.stat().st_size == 20
    assert not real.exists()
    assert all(p.exists() for p in keep), [p for p in keep if not p.exists()]
    assert not any(p.exists() for p in drop), [p for p in drop if p.exists()]


def test_slim_interpreter_keeps_libpython_when_linked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bundle, "IS_WINDOWS", False)
    python_dir = tmp_path / "python"
    exe = python_dir / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\x7fELF ... libpython3.12.so.1.0 ...")
    lib = _touch(python_dir / "lib" / "libpython3.12.so.1.0")

    bundle.slim_interpreter(python_dir)

    assert lib.exists()


def test_slim_interpreter_windows_keeps_stdlib(tmp_path: Path, monkeypatch) -> None:
    """Windows 的 glob 不区分大小写：POSIX 的 `lib/thread*` 曾命中 `Lib/threading.py`。

    rc.3 前的 CI 因此出现过解释器无法启动。
    """
    monkeypatch.setattr(bundle, "IS_WINDOWS", True)
    python_dir = tmp_path / "python"
    keep = [
        _touch(python_dir / "python.exe"),
        _touch(python_dir / "python312.dll"),  # Windows 的 python.exe 动态链接，必须保留
        _touch(python_dir / "Lib" / "threading.py"),
        _touch(python_dir / "Lib" / "tomllib" / "__init__.py"),
        _touch(python_dir / "Lib" / "site-packages" / "pkgconfig_stub.py"),
        _touch(python_dir / "DLLs" / "_ssl.pyd"),
    ]
    drop = [
        _touch(python_dir / "Lib" / "tkinter" / "__init__.py"),
        _touch(python_dir / "Lib" / "idlelib" / "idle.py"),
        _touch(python_dir / "tcl" / "tcl8.6" / "init.tcl"),
        _touch(python_dir / "DLLs" / "_tkinter.pyd"),
        _touch(python_dir / "DLLs" / "tcl86t.dll"),
        _touch(python_dir / "DLLs" / "tk86t.dll"),
    ]

    bundle.slim_interpreter(python_dir)

    assert all(p.exists() for p in keep), [p for p in keep if not p.exists()]
    assert not any(p.exists() for p in drop), [p for p in drop if p.exists()]


def test_copy_sources_bundles_agent_routes_and_skill(tmp_path: Path) -> None:
    ka = bundle.copy_sources(tmp_path)
    assert (ka / "web" / "agent_routes.py").is_file()
    skill = ka / "agent_skill" / "operate-knowledge-arch"
    for rel in ("SKILL.md", "references/setup.md", "assets/setup-prompt.zh.md",
                "scripts/knowledge_arch_client.py", "scripts/knowledge_arch_setup.py"):
        assert (skill / rel).is_file(), rel
    assert not list(skill.rglob("__pycache__"))


def test_every_web_module_is_bundled() -> None:
    """web/ 下的路由模块逐个列在 SOURCES 里：新增模块忘了登记，打包后的后端就起不来。"""
    modules = {f"web/{p.name}" for p in (ROOT / "web").glob("*.py") if p.name != "__init__.py"}
    assert modules <= set(bundle.SOURCES), modules - set(bundle.SOURCES)
