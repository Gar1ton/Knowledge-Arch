"""安装包内的字节码缓存重定向。

安装包不带 `.pyc`（体积），而应用目录在 macOS（签名后的 .app）和 Windows（Program Files）
上通常只读——Python 写不进 `__pycache__`，每次启动都会重新编译全部依赖。这里在任何业务
导入之前把 `sys.pycache_prefix` 指到用户缓存目录：首次启动编译一次，之后直接复用。

只在打包产物里生效（打包脚本在 ka/ 下写入 BUNDLE_MARKER）；开发环境保持 Python 默认行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_MARKER = ".ka_bundle"


def bundle_root() -> Path:
    """打包产物里 ka_server/ 的上一级（即 resources/ka/）。"""
    return Path(__file__).resolve().parent.parent


def redirect_bytecode_cache() -> str | None:
    """打包产物中把字节码缓存移到用户缓存目录；返回生效的前缀（未改动时为 None）。"""
    if sys.pycache_prefix is not None or not (bundle_root() / BUNDLE_MARKER).is_file():
        return None
    from platformdirs import user_cache_dir

    prefix = Path(user_cache_dir("Knowledge Arch", appauthor=False)) / "pycache"
    sys.pycache_prefix = str(prefix)
    return sys.pycache_prefix


__all__ = ["BUNDLE_MARKER", "bundle_root", "redirect_bytecode_cache"]
