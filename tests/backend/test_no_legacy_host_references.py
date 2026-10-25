"""守卫：除旧数据导入器外，仓库文本不得再出现旧宿主框架名。

Knowledge Arch 已与原聊天机器人宿主完全解耦；只有 `kacore/legacy_import/`（读取旧插件数据
目录）需要知道它的名字；README 的项目来源说明例外。名称在此拼接生成，避免本文件自身命中。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_NEEDLE = ("astr" + "bot").encode()
_ALLOWED_PREFIXES = ("kacore/legacy_import/",)
# README 的「项目来源」注明本项目承接自旧插件 v1.2.0（附插件仓库链接）。
_ALLOWED_FILES = frozenset({"README.md", "README_ZH.md"})


def _repo_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    )
    return [p for p in out.decode().split("\0") if p]


def test_no_legacy_host_name_outside_legacy_importer() -> None:
    offenders = []
    for rel in _repo_files():
        if rel.startswith(_ALLOWED_PREFIXES) or rel in _ALLOWED_FILES:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue  # 已删除但尚未提交的文件
        if _NEEDLE.decode() in rel.lower():
            offenders.append(rel)
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue  # 二进制（图片等）
        if _NEEDLE in data.lower():
            offenders.append(rel)
    assert offenders == []
