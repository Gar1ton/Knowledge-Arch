"""发布脚本 .github/scripts/build_main.py：精简 CHANGELOG、按清单生成 main 树、死链检查。"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_main", ROOT / ".github" / "scripts" / "build_main.py"
)
assert _spec and _spec.loader
build_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_main)

_CHANGELOG = """# CHANGELOG

> 说明段落。

## [Unreleased]

### 改进 (Changed)

- **安装包更小**：去掉重复解释器，
  体积下降。
- **前端**：`TopBar.tsx` 字标改用 Bitcount；其余不变。

## [v2.0.0] — 2026-10-25

### 新增 (Added)

- **`desktop/scripts/configure_release.py`**：CI 中补全配置；输出 feature。
- 版本统一为 2.0.0：五处版本号。

## [v2.0.0-dev.1] — 2026-10-24

- **开发期条目**
"""


def test_slim_changelog_keeps_releases_and_first_clauses() -> None:
    slim = build_main.slim_changelog(_CHANGELOG)
    assert "Unreleased" not in slim and "dev.1" not in slim and "开发期条目" not in slim
    assert "## [v2.0.0] — 2026-10-25" in slim
    assert "- `desktop/scripts/configure_release.py`：CI 中补全配置" in slim
    assert "- 版本统一为 2.0.0" in slim
    assert "输出 feature" not in slim and "五处版本号" not in slim


def test_slim_changelog_names_unreleased_after_tag() -> None:
    slim = build_main.slim_changelog(_CHANGELOG, "v2.0.0-rc.3")
    assert "## [v2.0.0-rc.3]" in slim
    assert "- 安装包更小\n" in slim  # 描述性的加粗句单独成条，续行不带出
    assert "- 前端：`TopBar.tsx` 字标改用 Bitcount\n" in slim


def test_slim_changelog_prefers_existing_tag_section() -> None:
    slim = build_main.slim_changelog(_CHANGELOG, "v2.0.0")
    assert slim.count("## [v2.0.0]") == 1 and "安装包更小" not in slim


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "developer")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "ci")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "developer")
    return repo


_MANIFEST = "/.release-exclude\n/TODO.md\n/docs/*\n!/docs/RELEASE.md\n"


def test_build_drops_manifest_paths_and_chains_on_parent(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, {
        ".release-exclude": _MANIFEST,
        "TODO.md": "todo",
        "CHANGELOG.md": _CHANGELOG,
        "README.md": "[release](./docs/RELEASE.md) · [site](https://example.com) · [top](#a)",
        "docs/RELEASE.md": "release",
        "docs/plan.md": "internal",
        "kacore/app.py": "print()",
    })
    parent = _git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(build_main, "ROOT", repo)

    sha = build_main.build("HEAD", parent, "v2.0.0-rc.3")

    files = _git(repo, "ls-tree", "-r", "--name-only", sha).split()
    assert sorted(files) == ["CHANGELOG.md", "README.md", "docs/RELEASE.md", "kacore/app.py"]
    assert _git(repo, "rev-parse", f"{sha}^").strip() == parent
    assert "## [v2.0.0-rc.3]" in _git(repo, "show", f"{sha}:CHANGELOG.md")
    message = _git(repo, "log", "-1", "--format=%B", sha)
    assert message.startswith("release: v2.0.0-rc.3") and parent in message
    assert _git(repo, "status", "--porcelain") == ""  # 工作区与 index 不受影响


def test_build_fails_on_links_to_removed_files(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, {
        ".release-exclude": _MANIFEST,
        "TODO.md": "todo",
        "CHANGELOG.md": _CHANGELOG,
        "README.md": "[roadmap](./TODO.md)",
        "kacore/README.md": "[plan](../docs/plan.md#x) · [code](./)",
        "docs/plan.md": "internal",
    })
    monkeypatch.setattr(build_main, "ROOT", repo)

    with pytest.raises(SystemExit) as err:
        build_main.build("HEAD", None, None)

    assert "README.md: ./TODO.md" in str(err.value)
    assert "kacore/README.md: ../docs/plan.md#x" in str(err.value)
    assert "./)" not in str(err.value)


def test_repository_manifest_produces_a_clean_tree() -> None:
    manifest = (ROOT / ".release-exclude").read_text(encoding="utf-8")
    for path in ("/CLAUDE.md", "/AGENTS.md", "/CONVENTIONS.md", "/TODO.md", "/ARCHITECTURE.md",
                 "/dev/", "/tools/", "/.github/scripts/", "/tests/backend/test_build_main.py"):
        assert path in manifest.splitlines(), path


def test_export_ignore_matches_manifest() -> None:
    """tag 源码包（git archive，遵守 export-ignore）与 main 删掉的是同一批文件。"""
    tracked = _git(ROOT, "ls-files").split("\n")
    tracked = [p for p in tracked if p] + [".release-exclude", "tests/backend/test_build_main.py"]
    excluded = set(
        _git(ROOT, "ls-files", "--cached", "--others", "--ignored",
             "--exclude-from=.release-exclude").split("\n")
    ) - {""}
    attrs = subprocess.run(
        ["git", "check-attr", "--stdin", "export-ignore"], cwd=ROOT, check=True,
        capture_output=True, text=True, input="\n".join(sorted(set(tracked))),
    ).stdout
    ignored = {line.split(": ")[0] for line in attrs.splitlines() if line.endswith(": set")}
    assert ignored == excluded & set(tracked)
