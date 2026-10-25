"""把后端打包成桌面壳的资源目录（在目标平台本机运行；CI 三平台各跑一次）。

产物（默认 `desktop/src-tauri/resources/`，已 gitignore）：

    python/     python-build-standalone 3.12（经 uv 下载）+ requirements.txt 的核心依赖
                （去掉字节码、测试、C 头文件与 pyarrow 用不到的扩展，见 prune_runtime）；
                site-packages 里写 `knowledge_arch.pth` 指向 ../ka，使 `python -I -m ka_server`
                可导入（-I 会忽略 PYTHONPATH，但仍处理 site-packages 里的 .pth）；
                uv 可执行文件放在解释器旁，供应用内安装可选组件（torch 等大包不进安装包）。
    ka/         kacore/、ka_server/、web/server.py、web/desktop_routes.py、migrations/；
                另有标记文件 `.ka_bundle`，运行时据此把字节码缓存写到用户缓存目录。
    frontend/   web/frontend/out/ 的静态导出（tauri.conf.json 的 frontendDist）。

用法：
    python desktop/scripts/bundle_python.py                 # 全部
    python desktop/scripts/bundle_python.py --skip-frontend # 前端另行构建时
需要：uv 在 PATH 上（或 --uv 指定）；前端已 `npm run build`（除非 --skip-frontend）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "desktop" / "src-tauri" / "resources"
PYTHON_VERSION = "3.12"
IS_WINDOWS = os.name == "nt"

# 只带运行所需的源码（相对仓库根）；目录按 _IGNORE 过滤缓存与测试残留。
SOURCES = ("kacore", "ka_server", "migrations", "web/server.py", "web/desktop_routes.py")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", "README.md")
PTH_NAME = "knowledge_arch.pth"
BUNDLE_MARKER = ".ka_bundle"  # 与 ka_server/bytecode_cache.py 一致

# 体积裁剪（对齐旧宿主桌面版：不带字节码）。字节码由运行时写到用户缓存目录，首启编译一次。
_BYTECODE_SUFFIXES = (".pyc", ".pyo")
# 只在 site-packages 里删：测试与 C 头文件运行期用不到（解释器自己的 include/ 留给源码编译的组件）。
_SITE_PRUNE_DIRS = frozenset({"tests", "include"})
# pyarrow 里 Milvus Lite 用不到的扩展：Flight RPC、S3 文件系统、C++ 自测库。它只用核心表、parquet
# 与 compute；这几个库只被各自的可选模块加载（pyarrow.fs 对 _s3fs 是 try-import）。
# substrait 不能删：pyarrow 26 的核心 lib 直接链接 libarrow_substrait。
_PYARROW_UNUSED = ("flight", "s3fs", "arrow_s3", "_pyarrow_cpp_tests")
# 标准库里后端用不到的部分（GUI、IDE、pip 引导、2to3、示例）；venv / sysconfig 保留给 uv。
_STDLIB_UNUSED = ("tkinter", "idlelib", "ensurepip", "lib2to3", "turtledemo")
# 解释器目录里 Tk 运行库与编译期文件，按平台分开：Windows 的 glob 不区分大小写，
# POSIX 的 `lib/thread*`（Tcl 的 thread 扩展）在 Windows 上会命中标准库 `Lib/threading.py`。
_POSIX_UNUSED_GLOBS = (
    "lib/libtcl*", "lib/libtk*", "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*",
    "lib/pkgconfig", "lib/python3.*/config-3.*", "lib/python3.*/lib-dynload/_tkinter*",
)
_WINDOWS_UNUSED_GLOBS = ("tcl", "DLLs/_tkinter*", "DLLs/tcl*", "DLLs/tk*")


def log(message: str) -> None:
    print(f"[bundle] {message}", flush=True)


def run(cmd: list[str], **kwargs: object) -> None:
    log(" ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)  # type: ignore[call-overload]


def python_executable(python_dir: Path) -> Path:
    return python_dir / "python.exe" if IS_WINDOWS else python_dir / "bin" / "python3"


def install_python(uv: str, out: Path) -> Path:
    """经 uv 下载 python-build-standalone，整理到 out/python。"""
    target = out / "python"
    if target.exists():
        shutil.rmtree(target)
    staging = out / ".python-download"
    shutil.rmtree(staging, ignore_errors=True)
    run([uv, "python", "install", PYTHON_VERSION, "--install-dir", str(staging), "--no-bin"])
    candidates = sorted(
        p for p in staging.iterdir() if p.is_dir() and p.name.startswith("cpython-")
        and not p.is_symlink()
    )
    real = [p for p in candidates if python_executable(p).exists()]
    if not real:
        raise SystemExit("uv did not produce a usable CPython install")
    shutil.move(str(real[-1]), str(target))
    shutil.rmtree(staging, ignore_errors=True)
    # 安装包里的运行时只读：去掉「外部管理」标记，否则构建期的 uv pip install 会拒绝写入。
    for marker in target.rglob("EXTERNALLY-MANAGED"):
        marker.unlink()
    return target


def python_output(python: Path, code: str) -> str:
    # -B：构建期不写字节码，产物保持「不带 .pyc」。
    out = subprocess.run(
        [str(python), "-I", "-B", "-c", code], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def site_packages(python: Path) -> Path:
    return Path(python_output(python, "import sysconfig; print(sysconfig.get_paths()['purelib'])"))


def install_requirements(uv: str, python: Path) -> None:
    run([
        uv, "pip", "install", "--no-config", "--python", str(python),
        "-r", str(ROOT / "requirements.txt"),
    ])
    # 运行期由 uv 安装组件，pip 用不上（还占十几 MB）。
    run([uv, "pip", "uninstall", "--no-config", "--python", str(python), "pip"])


def _tree_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def _delete(path: Path) -> int:
    if path.is_dir() and not path.is_symlink():
        size = _tree_size(path)
        shutil.rmtree(path)
        return size
    size = path.stat().st_size if path.exists() else 0
    path.unlink(missing_ok=True)
    return size


def prune_runtime(python_dir: Path, sp: Path) -> int:
    """裁掉运行期用不到的文件，返回释放的字节数。"""
    freed = 0
    for cache in sorted(python_dir.rglob("__pycache__"), reverse=True):
        freed += _delete(cache)
    for f in python_dir.rglob("*"):
        if f.suffix in _BYTECODE_SUFFIXES and f.is_file():
            freed += _delete(f)
    for d in sorted((p for p in sp.rglob("*") if p.is_dir() and p.name in _SITE_PRUNE_DIRS),
                    key=lambda p: len(p.parts)):
        if d.exists():
            freed += _delete(d)
    pyarrow = sp / "pyarrow"
    if pyarrow.is_dir():
        for f in list(pyarrow.iterdir()):
            if any(tag in f.name.lower() for tag in _PYARROW_UNUSED):
                freed += _delete(f)
    return freed


def slim_interpreter(python_dir: Path) -> int:
    """裁掉解释器里用不到的部分，返回释放的字节数。

    POSIX 的 bin/ 只留一个实体 python3：Tauri 复制资源时会把 python / python3 / python3.12
    这组符号链接展开成三份完整解释器；其余是带构建机绝对路径 shebang 的入口脚本，装好后也跑不了。
    python-build-standalone 的解释器静态链接 libpython，共享库只供嵌入用，解释器不引用它时一并删掉。
    """
    freed = 0
    stdlib = python_dir / ("Lib" if IS_WINDOWS else f"lib/python{PYTHON_VERSION}")
    for name in _STDLIB_UNUSED:
        if (stdlib / name).exists():
            freed += _delete(stdlib / name)
    for pattern in _WINDOWS_UNUSED_GLOBS if IS_WINDOWS else _POSIX_UNUSED_GLOBS:
        for path in sorted(python_dir.glob(pattern)):
            freed += _delete(path)
    if IS_WINDOWS:
        return freed
    bin_dir = python_dir / "bin"
    exe = python_executable(python_dir)
    real = exe.resolve()
    if exe.is_symlink():
        exe.unlink()
        real.rename(exe)
    for entry in list(bin_dir.iterdir()):
        if entry != exe:
            freed += _delete(entry)
    if b"libpython3" not in exe.read_bytes():
        for lib in sorted((python_dir / "lib").glob("libpython3*")):
            freed += _delete(lib)
    return freed


def strip_extensions(sp: Path) -> int:
    """Linux：strip site-packages 里的 .so，返回释放的字节数。

    wheel 里的扩展多数没 strip；解释器本身不能 strip（python-build-standalone 的解释器
    strip 后无法启动，这也是 AppImage 要设 NO_STRIP 的原因）。没有 strip 或单个文件失败时跳过，
    由冒烟测试兜底。
    """
    strip = shutil.which("strip")
    if not sys.platform.startswith("linux") or not strip:
        return 0
    freed = 0
    for lib in sorted(sp.rglob("*.so*")):
        if not lib.is_file() or lib.is_symlink() or not (lib.suffix == ".so" or ".so." in lib.name):
            continue
        before = lib.stat().st_size
        result = subprocess.run([strip, "--strip-unneeded", str(lib)], capture_output=True)
        if result.returncode == 0:
            freed += before - lib.stat().st_size
    return freed


def copy_sources(out: Path) -> Path:
    target = out / "ka"
    if target.exists():
        shutil.rmtree(target)
    for rel in SOURCES:
        src = ROOT / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=_IGNORE)
        else:
            shutil.copy2(src, dst)
    (target / BUNDLE_MARKER).write_text("bundled runtime\n", encoding="utf-8")
    return target


def write_pth(python: Path, ka_dir: Path) -> Path:
    """site-packages/knowledge_arch.pth → ka 目录的相对路径（安装位置无关）。"""
    sp = site_packages(python)
    rel = os.path.relpath(ka_dir, sp)
    pth = sp / PTH_NAME
    pth.write_text(rel + "\n", encoding="utf-8")
    return pth


def copy_uv(uv: str, python_dir: Path) -> Path:
    """uv 放在解释器旁：ExtrasManager.locate_uv 会在解释器同目录查找。"""
    exe_dir = python_executable(python_dir).parent
    dst = exe_dir / ("uv.exe" if IS_WINDOWS else "uv")
    shutil.copy2(uv, dst)
    if not IS_WINDOWS:
        dst.chmod(0o755)
    return dst


def copy_frontend(out: Path) -> None:
    src = ROOT / "web" / "frontend" / "out"
    if not (src / "index.html").exists():
        raise SystemExit("web/frontend/out is missing; run `npm run build` in web/frontend first")
    target = out / "frontend"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)


# 裁剪 pyarrow 后必须真实跑一遍 Milvus Lite：建库、写入、落盘（parquet）、带过滤检索（compute）。
_MILVUS_SMOKE = """
import os, tempfile
import pyarrow.compute, pyarrow.fs, pyarrow.parquet
from pymilvus import MilvusClient
client = MilvusClient(os.path.join(tempfile.mkdtemp(), "smoke.db"))
client.create_collection("smoke", dimension=4)
client.insert("smoke", [
    {"id": i, "vector": [float(i), 1.0, 0.0, 0.5], "tag": "odd" if i % 2 else "even"}
    for i in range(8)
])
client.flush("smoke")
hits = client.search(
    "smoke", data=[[3.0, 1.0, 0.0, 0.5]], limit=2, filter='tag == "odd"',
    output_fields=["tag"],
)
assert hits and hits[0] and hits[0][0]["id"] == 3, hits
client.close()
print("ok")
"""


def smoke_test(python: Path) -> None:
    """用 -I 启动打包后的解释器，确认后端可导入、核心依赖齐全、Milvus Lite 可用。"""
    code = (
        "import ka_server, kacore.app_initializer, web.server, aiohttp, pymupdf, pymilvus,"
        " milvus_lite; print(ka_server.APP_VERSION)"
    )
    log(f"smoke test ok, backend version {python_output(python, code)}")
    log(f"milvus lite round trip {python_output(python, _MILVUS_SMOKE)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--uv", default=shutil.which("uv"), help="uv executable (default: PATH)")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args(argv)
    if not args.uv:
        raise SystemExit("uv not found; install it or pass --uv")
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    python_dir = install_python(args.uv, out)
    python = python_executable(python_dir)
    install_requirements(args.uv, python)
    sp = site_packages(python)
    freed = prune_runtime(python_dir, sp)
    log(f"pruned {freed / 1048576:.0f} MiB (bytecode, tests, headers, unused pyarrow extensions)")
    freed = slim_interpreter(python_dir)
    log(f"slimmed interpreter by {freed / 1048576:.0f} MiB (aliases, libpython, Tk, idle)")
    freed = strip_extensions(sp)
    if freed:
        log(f"stripped extension modules, {freed / 1048576:.0f} MiB")
    ka_dir = copy_sources(out)
    pth = write_pth(python, ka_dir)
    log(f"wrote {pth}")
    copy_uv(args.uv, python_dir)
    if not args.skip_frontend:
        copy_frontend(out)
    smoke_test(python)
    log(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
