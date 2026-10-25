"""把后端打包成桌面壳的资源目录（在目标平台本机运行；CI 三平台各跑一次）。

产物（默认 `desktop/src-tauri/resources/`，已 gitignore）：

    python/     python-build-standalone 3.12（经 uv 下载）+ requirements.txt 的核心依赖；
                site-packages 里写 `knowledge_arch.pth` 指向 ../ka，使 `python -I -m ka_server`
                可导入（-I 会忽略 PYTHONPATH，但仍处理 site-packages 里的 .pth）；
                uv 可执行文件放在解释器旁，供应用内安装可选组件（torch 等大包不进安装包）。
    ka/         kacore/、ka_server/、web/server.py、web/desktop_routes.py、migrations/。
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "desktop" / "src-tauri" / "resources"
PYTHON_VERSION = "3.12"
IS_WINDOWS = os.name == "nt"

# 只带运行所需的源码（相对仓库根）；目录按 _IGNORE 过滤缓存与测试残留。
SOURCES = ("kacore", "ka_server", "migrations", "web/server.py", "web/desktop_routes.py")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", "README.md")
PTH_NAME = "knowledge_arch.pth"


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
    out = subprocess.run(
        [str(python), "-I", "-c", code], check=True, capture_output=True, text=True
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


def smoke_test(python: Path) -> None:
    """用 -I 启动打包后的解释器，确认后端可导入、核心依赖齐全。"""
    code = (
        "import ka_server, kacore.app_initializer, web.server, aiohttp, pymupdf, pymilvus,"
        " milvus_lite; print(ka_server.APP_VERSION)"
    )
    log(f"smoke test ok, backend version {python_output(python, code)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--uv", default=shutil.which("uv"), help="uv executable (default: PATH)")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--no-compile", action="store_true", help="skip .pyc precompilation")
    args = parser.parse_args(argv)
    if not args.uv:
        raise SystemExit("uv not found; install it or pass --uv")
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    python_dir = install_python(args.uv, out)
    python = python_executable(python_dir)
    install_requirements(args.uv, python)
    ka_dir = copy_sources(out)
    pth = write_pth(python, ka_dir)
    log(f"wrote {pth}")
    copy_uv(args.uv, python_dir)
    if not args.skip_frontend:
        copy_frontend(out)
    if not args.no_compile:
        # 预编译字节码：首启更快；用打包后的解释器编译，保证 magic number 一致。
        run([str(python), "-I", "-m", "compileall", "-q", str(ka_dir), str(site_packages(python))])
    smoke_test(python)
    log(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
