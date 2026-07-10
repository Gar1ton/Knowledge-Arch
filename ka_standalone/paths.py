"""数据目录解析（独立应用的 StarTools.get_data_dir 等价物）。

解析优先级：CLI ``--data-dir`` > 环境变量 ``KR_DATA_DIR`` > 默认 ``~/.knowledge-arch``。
三平台一致（Path.home() 在 Linux/macOS/Windows 均有定义），不引入额外依赖。
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_DATA_DIR = "KR_DATA_DIR"
DEFAULT_DATA_DIR_NAME = ".knowledge-arch"


def default_data_dir() -> Path:
    return Path.home() / DEFAULT_DATA_DIR_NAME


def resolve_data_dir(cli_value: str | None = None) -> Path:
    """按优先级解析数据目录；展开 ``~`` 并返回绝对路径（不创建目录）。"""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env_value = os.environ.get(ENV_DATA_DIR, "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return default_data_dir()


__all__ = ["ENV_DATA_DIR", "default_data_dir", "resolve_data_dir"]
