"""由 developer 的发布提交生成干净的 `main` 提交（CI 的 publish-main 作业调用，本地可预演）。

做法（全程用临时 index，不碰工作区与当前分支）：

1. 读入源提交的树，删去 `.release-exclude` 列出的路径（gitignore 语法，
   由 `git ls-files --ignored` 匹配）；
2. 把 CHANGELOG.md 换成精简版：只留正式版本（不含 `-dev.`），每条只留加粗的那句或首个分句；
3. 检查剩余 Markdown 的相对链接，指向被删文件即失败；
4. `git commit-tree` 生成提交，父提交是现有 main（线性接在后面，不需要 force push）。

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
CHANGELOG = "CHANGELOG.md"
_VERSION_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?P<rest>.*)$")
_BULLET = re.compile(r"^- (?P<body>.+)$")
_BOLD_LEAD = re.compile(r"^\*\*(?P<lead>.+?)\*\*")
_LABEL_MAX = 4  # 加粗部分不超过这么多字（如「前端」「桌面壳」）时视为标签
# Markdown 相对链接：[text](target)；跳过 http(s)、mailto、纯锚点。
_LINK = re.compile(r"\]\((?P<target>[^)\s]+)\)")


def git(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, env=env, input=stdin
    )
    return out.stdout


# ── CHANGELOG 精简 ────────────────────────────────────────────────────────────────


def _balanced(text: str) -> bool:
    return (text.count("（") == text.count("）") and text.count("(") == text.count(")")
            and text.count("`") % 2 == 0)


def _first_clause(text: str, limit: int = 90) -> str:
    """首个分句（中文分号 / 句号之前）。

    太长时退到括号与代码标记都闭合的最后一个逗号，找不到就保留整句。
    """
    clause = re.split(r"[；。]|; ", text, maxsplit=1)[0].strip()
    if len(clause) > limit:
        cuts = [
            m.start() for m in re.finditer(r"，|, ", clause[:limit])
            if _balanced(clause[:m.start()])
        ]
        if cuts:
            clause = clause[:cuts[-1]]
    return clause.rstrip("，,：: ")


def _join(lines: list[str]) -> str:
    """续行拼接：两侧都是 ASCII 时补空格，中文断行直接相连。"""
    joined = lines[0]
    for line in lines[1:]:
        sep = " " if joined[-1:].isascii() and line[:1].isascii() else ""
        joined += sep + line
    return joined


def _summarize_bullet(body: str) -> str:
    """加粗的描述句直接用；加粗的只是标签或路径（如「前端」「`kacore/x.py`」）时补上冒号后的首个分句。"""
    bold = _BOLD_LEAD.match(body)
    if not bold:  # 无加粗：冒号前是要点，冒号后是细节
        return _first_clause(re.split(r"：|: ", body, maxsplit=1)[0])
    lead = bold.group("lead")
    rest = body[bold.end():].lstrip("：: ")
    if rest and ("`" in lead or len(lead) <= _LABEL_MAX):
        return f"{lead}：{_first_clause(rest)}"
    return lead


def slim_changelog(text: str, tag: str | None = None) -> str:
    """只留正式版本的标题、小节与每条首句；`tag` 没有对应小节时，把 [Unreleased] 当作该版本。"""
    tagged = tag is not None and any(
        (m := _VERSION_HEADING.match(line)) and m.group("version") == tag
        for line in text.splitlines()
    )
    out = [
        "# CHANGELOG",
        "",
        "> 精简版，只列正式发布的版本。逐条细节见 `developer` 分支的 CHANGELOG.md。",
    ]
    keep = False
    bullet: list[str] | None = None  # 当前条目（含缩进续行）

    def flush() -> None:
        nonlocal bullet
        if bullet is not None:
            out.append(f"- {_summarize_bullet(_join(bullet))}")
            bullet = None

    for line in text.splitlines():
        if bullet is not None and line.startswith("  ") and line.strip():
            bullet.append(line.strip())
            continue
        flush()
        heading = _VERSION_HEADING.match(line)
        if heading:
            version = heading.group("version")
            if version == "Unreleased" and tag and not tagged:
                version, keep = tag, True
                line = f"## [{tag}]"
            else:
                keep = version != "Unreleased" and "-dev." not in version
            if keep:
                out += ["", line]
            continue
        if not keep:
            continue
        if line.startswith("### "):
            out += ["", line, ""]
        elif match := _BULLET.match(line):
            bullet = [match.group("body")]
    flush()
    return "\n".join(out).rstrip() + "\n"


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
        changelog = slim_changelog(git("show", f"{source_sha}:{CHANGELOG}"), tag)
        blob = git("hash-object", "-w", "--stdin", stdin=changelog).strip()
        git("update-index", "--cacheinfo", f"100644,{blob},{CHANGELOG}", env=env)
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
