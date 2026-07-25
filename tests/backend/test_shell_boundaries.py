"""壳边界守卫：锁死「一核两壳」的依赖方向（ARCHITECTURE.md §1 / §4.1）。

这些断言是架构腐坏的第一道防线——`kacore` 一旦重新 import 任何框架 SDK 或任何壳，
这里立刻红。修改本文件前请先确认你真的想改架构契约，而不是想绕过它。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KA_CORE = ROOT / "kacore"
KA_STANDALONE = ROOT / "standalone"
KA_PLUGIN = ROOT / "apps" / "plugin" / "ka_plugin"


def _imported_roots(py_file: Path) -> set[str]:
    """取该文件所有 import 的顶层模块名（含函数内的局部 import）。"""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _offenders(package: Path, forbidden: set[str]) -> list[str]:
    hits: list[str] = []
    for py_file in sorted(package.rglob("*.py")):
        bad = _imported_roots(py_file) & forbidden
        if bad:
            hits.append(f"{py_file.relative_to(ROOT)} imports {sorted(bad)}")
    return hits


def test_kacore_never_imports_astrbot() -> None:
    """核心必须框架中立：AstrBot 适配器归插件壳（apps/plugin/ka_plugin/）所有。"""
    assert _offenders(KA_CORE, {"astrbot"}) == []


def test_kacore_never_imports_either_shell() -> None:
    """依赖只能指向内层：核心不认识任何壳。"""
    assert _offenders(KA_CORE, {"ka_plugin", "standalone", "apps"}) == []


def test_the_two_shells_never_import_each_other() -> None:
    """两壳互不相识：各自只依赖 kacore。"""
    assert _offenders(KA_PLUGIN, {"standalone"}) == []
    assert _offenders(KA_STANDALONE, {"ka_plugin", "astrbot"}) == []
