#!/usr/bin/env python3
"""从 monorepo 生成旧仓 developer 形态源码树，可本地校验、可推送。

同步链（见 CLAUDE.md §1.1）：本仓 main（唯一开发真相源）──本脚本──►
Astrbot_Knowledge_Repository `developer` ──旧仓现行 build_published_tree 机制──► 正式 `main`。

树内容只由 ``release/developer-files.txt`` 决定；树根写入 ``.monorepo-source`` 标记
（monorepo 提交号 + manifest 哈希）供追溯。默认只构建不推送；``--push`` 需用户批准后使用。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_published_tree as bpt  # noqa: E402 - 复用 manifest 规则引擎

ROOT = Path(__file__).resolve().parent.parent
DEV_MANIFEST = ROOT / "release" / "developer-files.txt"
DEFAULT_OUTPUT = ROOT / "dist" / "developer-tree"
DEFAULT_REPO = "https://github.com/Gar1ton/Astrbot_Knowledge_Repository.git"
DEFAULT_BRANCH = "developer"
MARKER = ".monorepo-source"

REQUIRED_DEV = {
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "README.md",
    "CHANGELOG.md",
    "requirements.txt",
    "requirements-additional.txt",
    "LICENSE",
    "pyproject.toml",
    "tools/build_published_tree.py",
    "release/published-files.txt",
    "release/AGENTS.published.md",
    "web/server.py",
    "kacore/config.py",
    "pages/index.html",
    ".agents/skills/operate-knowledge-arch/SKILL.md",
    ".agents/skills/operate-knowledge-arch/agents/openai.yaml",
    ".agents/skills/operate-knowledge-arch/scripts/knowledge_arch_client.py",
    ".agents/skills/operate-knowledge-arch/references/research.md",
    ".agents/skills/operate-knowledge-arch/references/settings.md",
    MARKER,
}
# monorepo 专属内容绝不进入旧仓 developer 树。
FORBIDDEN_DEV_PREFIXES = ("apps/", "standalone/", "scripts/", "docs/")
FORBIDDEN_DEV_FILES = {"CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "CONVENTIONS.md", "TODO.md"}


def _validate_dev_tree(output: Path) -> None:
    files = bpt._tree_files(output)
    missing = sorted(REQUIRED_DEV - files)
    forbidden = sorted(
        path
        for path in files
        if path in FORBIDDEN_DEV_FILES or path.startswith(FORBIDDEN_DEV_PREFIXES)
    )
    if missing or forbidden:
        detail = []
        if missing:
            detail.append(f"缺少必需文件：{missing}")
        if forbidden:
            detail.append(f"包含禁止文件：{forbidden}")
        raise RuntimeError("；".join(detail))


def build_dev_tree(source: str, output: Path) -> str:
    """按 developer manifest 从 ``source`` 生成同步树，返回源提交号。"""
    bpt.MANIFEST = DEV_MANIFEST
    bpt.SENTINEL = MARKER  # _prepare_output 以此识别可安全清理的生成目录
    revision = bpt._source_revision(source)
    selected = bpt._select_files(source)
    bpt._prepare_output(output)
    for target, original in selected.items():
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bpt._read_source_file(source, original))
    (output / MARKER).write_text(
        f"source=Knowledge-Arch@{revision}\n"
        f"manifest_sha256={hashlib.sha256(DEV_MANIFEST.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    _validate_dev_tree(output)
    return revision


def diff_against(output: Path, other: Path) -> int:
    """与本地旧仓工作区比对（信息性输出，首次同步前用于预览变更面）。"""
    result = subprocess.run(
        [
            "diff", "-r", "--brief",
            "--exclude=.git",
            f"--exclude={MARKER}",
            "--exclude=__pycache__",
            "--exclude=dist",
            "--exclude=node_modules",
            str(other), str(output),
        ],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    print(f"---- diff 条目数：{len(lines)}")
    return result.returncode


def push_dev_tree(output: Path, repo: str, branch: str, revision: str) -> None:
    """临时 clone 旧仓，覆写 ``branch`` 内容为生成树并推送。"""
    with tempfile.TemporaryDirectory(prefix="ka-sync-") as tmp:
        clone = Path(tmp) / "repo"
        subprocess.check_call(
            ["git", "clone", "--depth", "1", "--branch", branch, repo, str(clone)]
        )
        for entry in clone.iterdir():
            if entry.name == ".git":
                continue
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        for path in sorted(output.rglob("*")):
            if not path.is_file():
                continue
            destination = clone / path.relative_to(output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        subprocess.check_call(["git", "-C", str(clone), "add", "-A"])
        status = subprocess.check_output(
            ["git", "-C", str(clone), "status", "--porcelain"]
        ).decode()
        if not status.strip():
            print("developer 已与生成树一致，无需推送。")
            return
        subprocess.check_call(
            [
                "git", "-C", str(clone),
                "commit", "-m", f"sync: from Knowledge-Arch@{revision}",
            ]
        )
        subprocess.check_call(["git", "-C", str(clone), "push", "origin", branch])
        print(f"已推送 developer 同步树（source=Knowledge-Arch@{revision}）。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="HEAD", help="monorepo commit/ref；预览可用 WORKTREE")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check-only", action="store_true", help="仅构建并校验，不比对不推送")
    parser.add_argument(
        "--diff-against", type=Path, metavar="DIR",
        help="与本地旧仓 developer 工作区目录比对（信息性，不作为闸门）",
    )
    parser.add_argument("--push", action="store_true", help="推送到旧仓（须先取得用户批准）")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    args = parser.parse_args()

    try:
        revision = build_dev_tree(args.source, args.output)
        files = bpt._tree_files(args.output)
        print(f"developer tree: {len(files)} files, source=Knowledge-Arch@{revision}")
        if args.diff_against is not None:
            diff_against(args.output, args.diff_against)
        if args.push:
            push_dev_tree(args.output, args.repo, args.branch, revision)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"developer sync build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
