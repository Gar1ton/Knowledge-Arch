"""AI 助手 skill：定位随应用分发的 skill、生成首次配置 prompt、一键安装到 Codex / Claude Code。

skill 目录 `operate-knowledge-arch` 在开发仓库里位于 `.agents/skills/`，打包后位于 `ka/agent_skill/`
（`desktop/scripts/bundle_python.py`）。客户端只用标准库，统一由应用自带的解释器运行，
用户无需安装 Python。

一键安装把 skill 复制到 Codex（`$CODEX_HOME/skills`，默认 `~/.codex/skills`）与 Claude Code
（`$CLAUDE_CONFIG_DIR/skills`，默认 `~/.claude/skills`），并把 SKILL.md 里的命令占位块替换为
本机的绝对命令。目录里放标记文件 `.knowledge-arch-skill`：只覆盖或删除带这个标记的目录，
不碰用户同名的其它 skill。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

SKILL_NAME = "operate-knowledge-arch"
CLIENT_RELATIVE = Path("scripts") / "knowledge_arch_client.py"
INSTALL_MARKER = ".knowledge-arch-skill"
_ROOT = Path(__file__).resolve().parents[1]  # 开发：仓库根；打包：ka/
_SOURCE_CANDIDATES = (
    _ROOT / "agent_skill" / SKILL_NAME,
    _ROOT / ".agents" / "skills" / SKILL_NAME,
)
_COMMAND_BLOCK = re.compile(
    r"<!-- ka:client-command -->.*?<!-- /ka:client-command -->", re.DOTALL
)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")
TARGETS = ("codex", "claude")


def skill_source_dir() -> Path:
    for candidate in _SOURCE_CANDIDATES:
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise FileNotFoundError(f"the {SKILL_NAME} skill is not bundled with this build")


def _under_home(path: Path | str) -> str | None:
    """家目录下的路径返回其余部分（以 / 开头），否则 None。"""
    text, home = str(path), str(Path.home())
    if len(home) > 1 and (text == home or text.startswith(home + os.sep)):
        return text[len(home):]
    return None


def _quote(path: Path | str) -> str:
    """命令里的路径：POSIX 家目录下写成 "$HOME/…"，prompt 发给 AI 服务商时不带用户名；

    其余 POSIX 路径用单引号；Windows 用双引号（PowerShell 与 cmd 都认；Git Bash 对 $HOME 的
    展开形式不同，Windows 上保留绝对路径）。
    """
    if os.name == "nt":
        return f'"{path}"'
    rest = _under_home(path)
    if rest is None:
        return shlex.quote(str(path))
    for char in ("\\", '"', "$", "`"):
        rest = rest.replace(char, "\\" + char)
    return f'"$HOME{rest}"'


def _display(path: Path) -> str:
    """给人 / AI 阅读的路径：家目录显示为 ~（Windows 同样处理）。"""
    rest = _under_home(path)
    return str(path) if rest is None else "~" + rest


def client_command(skill_dir: Path, connection_file: Path, python: Path | None = None) -> str:
    """应用自带解释器运行客户端的完整命令（不含子命令）。-I：不受用户环境变量与 site 影响。"""
    exe = python or Path(sys.executable)
    return (
        f"{_quote(exe)} -I {_quote(skill_dir / CLIENT_RELATIVE)}"
        f" --connection-file {_quote(connection_file)}"
    )


# ── 首次配置 prompt ────────────────────────────────────────────────────

def build_prompt(lang: str, *, skill_dir: Path, connection_file: Path) -> str:
    """按界面语言渲染 skill 自带的模板（`assets/setup-prompt.<lang>.md`）。"""
    name = f"setup-prompt.{'zh' if lang == 'zh' else 'en'}.md"
    template = (skill_source_dir() / "assets" / name).read_text(encoding="utf-8")
    # 用 replace 而不是 format：模板正文里出现花括号也不会出错。
    return template.replace("{skill_md}", _display(skill_dir / "SKILL.md")).replace(
        "{command}", client_command(skill_dir, connection_file)
    )


# ── 一键安装 ──────────────────────────────────────────────────────────


def target_root(target: str) -> Path:
    if target == "codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "skills"
    if target == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"
    raise ValueError(f"unknown skill target: {target}")


def _read_marker(directory: Path) -> dict[str, str] | None:
    try:
        data = json.loads((directory / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def install_status(version: str) -> dict[str, dict[str, object]]:
    """每个目标：未安装 / 已安装（是否为当前版本）/ 同名但不是本应用装的。"""
    result: dict[str, dict[str, object]] = {}
    for target in TARGETS:
        directory = target_root(target) / SKILL_NAME
        marker = _read_marker(directory)
        if not directory.exists():
            state = "missing"
        elif marker is None:
            state = "foreign"
        else:
            state = "current" if marker.get("version") == version else "outdated"
        result[target] = {"state": state, "path": str(directory)}
    return result


def render_skill_md(text: str, command: str) -> str:
    block = (
        "<!-- ka:client-command -->\n"
        "Run the client with Knowledge Arch's bundled Python on this computer:\n\n"
        f"```\n{command} <command>\n```\n"
        "<!-- /ka:client-command -->"
    )
    return _COMMAND_BLOCK.sub(lambda _m: block, text, count=1)


def install_skill(
    targets: list[str], *, version: str, connection_file: Path, python: Path | None = None
) -> dict[str, dict[str, object]]:
    """复制 skill 到各目标；目标里已有同名但不是本应用装的目录时跳过并报告。"""
    source = skill_source_dir()
    results: dict[str, dict[str, object]] = {}
    for target in targets:
        root = target_root(target)
        dest = root / SKILL_NAME
        if dest.exists() and _read_marker(dest) is None:
            results[target] = {"state": "foreign", "path": str(dest)}
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, ignore=_IGNORE)
        command = client_command(dest, connection_file, python)
        skill_md = dest / "SKILL.md"
        skill_md.write_text(
            render_skill_md(skill_md.read_text(encoding="utf-8"), command), encoding="utf-8"
        )
        (dest / INSTALL_MARKER).write_text(
            json.dumps({"version": version, "installed_by": "Knowledge Arch"}) + "\n",
            encoding="utf-8",
        )
        results[target] = {"state": "current", "path": str(dest)}
    return results


def refresh_installed(*, version: str, connection_file: Path) -> list[str]:
    """启动时刷新本应用装过的 skill：AppImage 每次挂载路径不同、升级后安装路径与版本也会变。

    只处理带标记文件的目录（用户自己的同名 skill 不动），返回刷新过的目标。
    """
    targets = [t for t, info in install_status(version).items()
               if info["state"] in ("current", "outdated")]
    if targets:
        install_skill(targets, version=version, connection_file=connection_file)
    return targets


__all__ = [
    "SKILL_NAME",
    "TARGETS",
    "build_prompt",
    "client_command",
    "install_skill",
    "install_status",
    "refresh_installed",
    "render_skill_md",
    "skill_source_dir",
]
