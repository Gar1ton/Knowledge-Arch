"""组件安装失败归类：把 uv 的输出归成用户看得懂、知道下一步做什么的几类。

只看文本，不依赖 uv 的退出码细节；规则按「越确定越靠前」排列（磁盘满时往往也伴随网络重试
的报错，先认磁盘）。界面按 `reason` 显示本地化说明，`detail` 是最相关的几行原文，供展开查看。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

REASON_DISK_FULL = "disk_full"
REASON_PERMISSION = "permission"
REASON_NETWORK = "network"
REASON_NO_WHEEL = "no_wheel"
REASON_CONFLICT = "conflict"
REASON_BUILD = "build"
REASON_UNKNOWN = "unknown"

_MAX_DETAIL_LINES = 8

# (reason, 说明, 匹配规则)；按顺序取第一个命中的。
_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        REASON_DISK_FULL,
        "Not enough disk space: free up space on the data folder's drive and try again.",
        re.compile(r"no space left on device|disk quota exceeded|os error (28|112|122)", re.I),
    ),
    (
        REASON_PERMISSION,
        "No write permission: the data folder may be in use by another program or read-only.",
        re.compile(r"permission denied|access is denied|os error (5|13)\b", re.I),
    ),
    (
        REASON_NETWORK,
        "Network error: check your connection or proxy and try again.",
        re.compile(
            r"failed to fetch|error sending request|dns error|tcp connect error|"
            r"connection (reset|refused|closed)|timed out|network is unreachable|"
            r"certificate verify failed|invalid peer certificate",
            re.I,
        ),
    ),
    (
        REASON_NO_WHEEL,
        "No package is available for this system or Python version.",
        re.compile(
            r"no wheels with a matching (platform|python)|"
            r"doesn't have a source distribution or wheel for the current platform|"
            r"only has wheels for the following platforms",
            re.I,
        ),
    ),
    (
        REASON_CONFLICT,
        "Dependency conflict: this component needs versions that clash with the current "
        "environment.",
        re.compile(r"no solution found when resolving|requirements are unsatisfiable", re.I),
    ),
    (
        REASON_BUILD,
        "It had to be built from source and the build failed (no compiler on this machine).",
        re.compile(r"failed to build|failed to prepare distributions|build backend failed", re.I),
    ),
)

_ERROR_LINE = re.compile(r"^\s*(error|×|╰─▶|caused by|help:)", re.I)


@dataclass(frozen=True)
class InstallFailure:
    reason: str
    message: str
    detail: tuple[str, ...]


def _detail(lines: Sequence[str], pattern: re.Pattern[str] | None) -> tuple[str, ...]:
    """最相关的几行：命中规则的行及其后续，否则取报错行，再否则取末尾。"""
    picked = [ln for ln in lines if (pattern and pattern.search(ln)) or _ERROR_LINE.match(ln)]
    return tuple((picked or list(lines))[-_MAX_DETAIL_LINES:])


def classify_install_failure(lines: Sequence[str], returncode: int | None) -> InstallFailure:
    """把安装器输出归类；都不命中时归为 unknown 并附上末尾几行。"""
    text = "\n".join(lines)
    for reason, message, pattern in _RULES:
        if pattern.search(text):
            return InstallFailure(reason, message, _detail(lines, pattern))
    suffix = f" (exit code {returncode})" if returncode is not None else ""
    return InstallFailure(
        REASON_UNKNOWN, f"Installation failed{suffix}; see the log.", _detail(lines, None)
    )


__all__ = [
    "REASON_BUILD",
    "REASON_CONFLICT",
    "REASON_DISK_FULL",
    "REASON_NETWORK",
    "REASON_NO_WHEEL",
    "REASON_PERMISSION",
    "REASON_UNKNOWN",
    "InstallFailure",
    "classify_install_failure",
]
