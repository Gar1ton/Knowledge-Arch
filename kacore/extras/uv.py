"""用 uv 把可选组件装进数据目录（生产实现）。

命令形如 `uv pip install --no-config --target <dir> --python <当前解释器> <specs>`：
- `--python` 指向后端正在使用的解释器，保证选中的 wheel 与它的版本 / 平台匹配；
- `--no-config` 忽略用户机器上的 uv 配置，结果只由本应用决定。
uv 可执行文件的位置可配置（`ka_server --uv` / 环境变量 `KA_UV`），否则在 PATH 与解释器旁边查找；
安装包会把 uv 一并带上（P5）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from kacore.extras.base import ExtrasInstaller, ExtrasUnavailableError, LineSink

logger = logging.getLogger("ExtrasInstaller")

ENV_UV_PATH = "KA_UV"
_UV_NAMES = ("uv.exe", "uv") if os.name == "nt" else ("uv",)


def locate_uv(explicit: str | None = None) -> str | None:
    """按「显式参数 → KA_UV → 解释器同目录 → PATH」的顺序找 uv；找不到返回 None。"""
    for candidate in (explicit, os.environ.get(ENV_UV_PATH)):
        if candidate:
            path = Path(candidate).expanduser()
            return str(path) if path.is_file() else None
    exe_dir = Path(sys.executable).resolve().parent
    for directory in (exe_dir, exe_dir.parent, exe_dir.parent / "bin"):
        for name in _UV_NAMES:
            if (directory / name).is_file():
                return str(directory / name)
    found = shutil.which("uv")
    return found or None


class UvInstaller(ExtrasInstaller):
    """`uv pip install --target` 安装器。"""

    def __init__(self, uv_path: str | None, *, python: str | None = None) -> None:
        self._uv_path = uv_path
        self._python = python or sys.executable

    @property
    def available(self) -> bool:
        return bool(self._uv_path) and Path(str(self._uv_path)).is_file()

    def describe(self) -> str:
        return self._uv_path or "uv not found"

    def command(
        self,
        specs: Sequence[str],
        target: Path,
        *,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """拼出完整命令（单独暴露便于测试与诊断日志）。"""
        cmd = [
            str(self._uv_path),
            "pip",
            "install",
            "--no-config",
            "--target",
            str(target),
            "--python",
            self._python,
        ]
        cmd += [*extra_args, *specs]
        return cmd

    async def install(
        self,
        specs: Sequence[str],
        target: Path,
        *,
        extra_args: Sequence[str] = (),
        on_line: LineSink | None = None,
    ) -> int:
        if not self.available:
            raise ExtrasUnavailableError(
                "uv not found, so components cannot be installed: reinstall Knowledge Arch, or "
                f"point the {ENV_UV_PATH} environment variable at uv."
            )
        cmd = self.command(specs, target, extra_args=extra_args)
        logger.info("installing component: %s", " ".join(cmd))
        env = {**os.environ, "UV_NO_PROGRESS": "1", "PYTHONNOUSERSITE": "1"}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line and on_line is not None:
                on_line(line)
        return await proc.wait()


__all__ = ["ENV_UV_PATH", "UvInstaller", "locate_uv"]
