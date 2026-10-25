"""由 developer 的发布提交生成干净的 `main` 提交（CI 的 publish-main 作业调用，本地可预演）。

做法（全程用临时 index，不碰工作区与当前分支）：

1. 读入源提交的树，删去 `.release-exclude` 列出的路径（gitignore 语法，
   由 `git ls-files --ignored` 匹配）；
2. 检查剩余 Markdown 的相对链接，指向被删文件即失败；
3. `git commit-tree` 生成提交，父提交是现有 main（线性接在后面，不需要 force push）。

只输出新提交的 sha，不 push；推送由流水线（或本地人工）另行执行。

用法：
    python .github/scripts/build_main.py --tag v2.0.0-rc.3 --parent origin/main
    # 预演：只列出 main 的文件，不生成提交
    python .github/scripts/build_main.py --tag v2.0.0-rc.3 --parent origin/main --list
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ".release-exclude"
# Markdown 相对链接：[text](target)；跳过 http(s)、mailto、纯锚点。
_LINK = re.compile(r"\]\((?P<target>[^)\s]+)\)")


def git(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, env=env, input=stdin
    )
    return out.stdout


# ── 树的生成与检查 ────────────────────────────────────────────────────────────────


def excluded_paths(env: dict[str, str], manifest: Path) -> list[str]:
    listing = git("ls-files", "-z", "--cached", "--ignored", f"--exclude-from={manifest}", env=env)
    return [p for p in listing.split("\0") if p]


def dead_links(env: dict[str, str]) -> list[str]:
    """剩余 Markdown 里指向不存在路径的相对链接，格式 `file: target`。"""
    files = [p for p in git("ls-files", "-z", env=env).split("\0") if p]
    present = set(files)
    dirs = {str(parent) for p in files for parent in PurePosixPath(p).parents}
    problems = []
    for md in (p for p in files if p.endswith(".md")):
        text = git("show", f":{md}", env=env)
        for match in _LINK.finditer(text):
            target = match.group("target").split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(md), target))
            if resolved not in present and resolved.rstrip("/") not in dirs:
                problems.append(f"{md}: {match.group('target')}")
    return problems


def build(
    source: str,
    parent: str | None,
    tag: str | None,
    *,
    manifest_path: Path | None = None,
    list_only: bool = False,
) -> str:
    source_sha = git("rev-parse", "--verify", f"{source}^{{commit}}").strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("read-tree", source_sha, env=env)
        manifest = Path(tmp) / MANIFEST
        if manifest_path is None:  # 默认用源提交里的清单；预演未提交的清单时用 --manifest
            manifest.write_text(git("show", f"{source_sha}:{MANIFEST}"), encoding="utf-8")
        else:
            manifest.write_bytes(manifest_path.read_bytes())
        removed = excluded_paths(env, manifest)
        if removed:
            git("rm", "--cached", "--quiet", "--pathspec-from-file=-", "--pathspec-file-nul",
                env=env, stdin="\0".join(removed))
        problems = dead_links(env)
        if problems:
            raise SystemExit("dead links in the main tree:\n  " + "\n  ".join(problems))
        if list_only:
            print(git("ls-files", env=env), end="")
            return ""
        tree = git("write-tree", env=env).strip()
    title = f"release: {tag}" if tag else "release"
    message = f"{title}\n\nGenerated from developer {source_sha}.\n"
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args[2:2] = ["-p", git("rev-parse", "--verify", f"{parent}^{{commit}}").strip()]
    return git(*args).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="HEAD", help="developer commit (default: HEAD)")
    parser.add_argument("--parent", help="current main, e.g. origin/main (omit for a root commit)")
    parser.add_argument("--tag", help="release tag, e.g. v2.0.0-rc.3")
    parser.add_argument(
        "--manifest", type=Path, help=f"exclude list (default: {MANIFEST} in source)"
    )
    parser.add_argument("--list", action="store_true", help="print the main file list, no commit")
    args = parser.parse_args(argv)
    sha = build(
        args.source, args.parent, args.tag, manifest_path=args.manifest, list_only=args.list
    )
    if sha:
        print(sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
