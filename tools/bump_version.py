"""双版本线 bump 工具（monorepo）。

- ``--target plugin``（默认）：插件版本线 v1.0.x。版本源 = ``apps/plugin/metadata.yaml``；
  同步更新 ``apps/plugin/main.py`` ``_PLUGIN_VERSION``、``apps/plugin/README.md`` 徽章、
  ``apps/plugin/CHANGELOG.md`` 的 ``[Unreleased]`` 收口。
- ``--target standalone``：独立应用版本线 v0.1.x。版本源 = 根 ``pyproject.toml``
  ``[project].version``；同步更新根 ``README.md`` standalone 徽章、``standalone/__init__.py``
  ``__version__``（文件存在时）、根 ``CHANGELOG.md`` 收口、``TODO.md`` 顶部版本标题。
"""
from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "apps" / "plugin"

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
METADATA_VERSION_RE = re.compile(r"^(version:\s*)v?(\d+\.\d+\.\d+)(.*)$", re.MULTILINE)
MAIN_VERSION_RE = re.compile(r'^(_PLUGIN_VERSION\s*=\s*")v?\d+\.\d+\.\d+(".*)$', re.MULTILINE)
PYPROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*")v?(\d+\.\d+\.\d+)(")$', re.MULTILINE)
INIT_VERSION_RE = re.compile(r'^(__version__\s*=\s*")v?\d+\.\d+\.\d+(")$', re.MULTILINE)
PLUGIN_BADGE_RE = re.compile(
    r"(\[!\[version\]\(https://img\.shields\.io/badge/版本-)v?\d+\.\d+\.\d+"
    r"(-blueviolet\)\]\(metadata\.yaml\))"
)
STANDALONE_BADGE_RE = re.compile(
    r"(\[!\[standalone\]\(https://img\.shields\.io/badge/standalone-)v?\d+\.\d+\.\d+"
    r"(-orange\)\]\(pyproject\.toml\))"
)
TODO_HEADING_RE = re.compile(r"^(##\s+)v?(\d+\.\d+\.\d+)(\s+.+)$", re.MULTILINE)
CHANGELOG_UNRELEASED_RE = re.compile(r"^## \[Unreleased\]\s*", re.MULTILINE)
CHANGELOG_RELEASE_RE = re.compile(r"^## \[v\d+\.\d+\.\d+\]", re.MULTILINE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str, *, dry_run: bool) -> None:
    if not dry_run:
        path.write_text(content, encoding="utf-8", newline="\n")


def _parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid version: {value!r}; expected vX.Y.Z or X.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def _format_version(parts: tuple[int, int, int]) -> str:
    return f"v{parts[0]}.{parts[1]}.{parts[2]}"


def _resolve_target_version(current: str, bump: str) -> str:
    if VERSION_RE.fullmatch(bump):
        return _format_version(_parse_version(bump))
    major, minor, patch = _parse_version(current)
    if bump == "major":
        return _format_version((major + 1, 0, 0))
    if bump == "minor":
        return _format_version((major, minor + 1, 0))
    if bump == "patch":
        return _format_version((major, minor, patch + 1))
    raise ValueError("bump must be one of: patch, minor, major, or vX.Y.Z")


def _sub_once(path: Path, pattern: re.Pattern[str], replacement: str, *, dry_run: bool) -> bool:
    content = _read(path)
    updated, count = pattern.subn(replacement, content, count=1)
    if count != 1:
        raise RuntimeError(f"Failed to update version in {path}")
    _write(path, updated, dry_run=dry_run)
    return updated != content


def _release_changelog(path: Path, target: str, release_date: str, *, dry_run: bool) -> bool:
    """把 ``[Unreleased]`` 内容收口为版本标题；容忍尚无历史版本标题的新 CHANGELOG。"""
    content = _read(path)
    unreleased = CHANGELOG_UNRELEASED_RE.search(content)
    if not unreleased:
        raise RuntimeError(f"{path} does not contain ## [Unreleased]")

    next_release = CHANGELOG_RELEASE_RE.search(content, unreleased.end())
    body_end = next_release.start() if next_release else len(content)
    body = content[unreleased.end() : body_end].strip()
    if not body:
        return False

    replacement = f"## [Unreleased]\n\n## [{target}] — {release_date}\n\n{body}\n"
    if next_release:
        replacement += "\n"
    updated = content[: unreleased.start()] + replacement + content[body_end:]
    _write(path, updated, dry_run=dry_run)
    return updated != content


@dataclass(frozen=True)
class TargetSpec:
    """一条版本线：版本源读取 + 各文件更新动作。"""

    name: str
    current_version: Callable[[], str]
    updaters: dict[str, Callable[[str, str, bool], bool]]


def _plugin_current() -> str:
    match = METADATA_VERSION_RE.search(_read(PLUGIN_DIR / "metadata.yaml"))
    if not match:
        raise RuntimeError("apps/plugin/metadata.yaml does not contain a version line")
    return f"v{match.group(2)}"


def _standalone_current() -> str:
    match = PYPROJECT_VERSION_RE.search(_read(ROOT / "pyproject.toml"))
    if not match:
        raise RuntimeError("pyproject.toml does not contain a [project] version line")
    return f"v{match.group(2)}"


def _standalone_init(target: str, release_date: str, dry_run: bool) -> bool:
    init_path = ROOT / "standalone" / "__init__.py"
    if not init_path.exists():
        return False
    return _sub_once(init_path, INIT_VERSION_RE, rf"\g<1>{target}\g<2>", dry_run=dry_run)


TARGETS = {
    "plugin": TargetSpec(
        name="plugin",
        current_version=_plugin_current,
        updaters={
            "apps/plugin/metadata.yaml": lambda t, d, dry: _sub_once(
                PLUGIN_DIR / "metadata.yaml", METADATA_VERSION_RE, rf"\g<1>{t}\g<3>", dry_run=dry
            ),
            "apps/plugin/main.py": lambda t, d, dry: _sub_once(
                PLUGIN_DIR / "main.py", MAIN_VERSION_RE, rf"\g<1>{t}\g<2>", dry_run=dry
            ),
            "apps/plugin/README.md": lambda t, d, dry: _sub_once(
                PLUGIN_DIR / "README.md", PLUGIN_BADGE_RE, rf"\g<1>{t}\g<2>", dry_run=dry
            ),
            "apps/plugin/CHANGELOG.md": lambda t, d, dry: _release_changelog(
                PLUGIN_DIR / "CHANGELOG.md", t, d, dry_run=dry
            ),
        },
    ),
    "standalone": TargetSpec(
        name="standalone",
        current_version=_standalone_current,
        updaters={
            "pyproject.toml": lambda t, d, dry: _sub_once(
                ROOT / "pyproject.toml",
                PYPROJECT_VERSION_RE,
                rf"\g<1>{t.lstrip('v')}\g<3>",
                dry_run=dry,
            ),
            "README.md": lambda t, d, dry: _sub_once(
                ROOT / "README.md", STANDALONE_BADGE_RE, rf"\g<1>{t}\g<2>", dry_run=dry
            ),
            "standalone/__init__.py": _standalone_init,
            "CHANGELOG.md": lambda t, d, dry: _release_changelog(
                ROOT / "CHANGELOG.md", t, d, dry_run=dry
            ),
            "TODO.md": lambda t, d, dry: _sub_once(
                ROOT / "TODO.md", TODO_HEADING_RE, rf"\g<1>{t}\g<3>", dry_run=dry
            ),
        },
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump one version line (plugin or standalone) across its source files."
    )
    parser.add_argument(
        "bump",
        choices=("patch", "minor", "major"),
        nargs="?",
        default="patch",
        help="SemVer part to bump (default: patch).",
    )
    parser.add_argument(
        "--target",
        choices=tuple(TARGETS),
        default="plugin",
        help="Which version line to bump (default: plugin).",
    )
    parser.add_argument(
        "--version",
        help="Explicit target version, e.g. v0.1.0. Overrides the bump argument.",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Release date for CHANGELOG (default: today).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned version change without writing files.",
    )
    args = parser.parse_args()

    spec = TARGETS[args.target]
    current = spec.current_version()
    target = _resolve_target_version(current, args.version or args.bump)

    mode = "Would bump" if args.dry_run else "Bumped"
    print(f"{mode} [{spec.name}] {current} -> {target}")
    for label, updater in spec.updaters.items():
        did_change = updater(target, args.date, args.dry_run)
        print(f"- {label}: {'changed' if did_change else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
