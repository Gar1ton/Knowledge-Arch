"""可选组件安装器的内存替身（测试用，不起子进程、不联网）。

按 `packages` 映射在 target 里写出假的顶层包（`<name>/__init__.py`），让「装完就能 import」
的契约可以在单测里验证；`returncode` 非 0 时模拟安装失败（仍会写半截文件，检验回滚）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kacore.extras.base import ExtrasInstaller, ExtrasUnavailableError, LineSink


@dataclass
class FakeExtrasInstaller(ExtrasInstaller):
    packages: dict[str, str] = field(default_factory=dict)  # spec → 顶层 import 名
    returncode: int = 0
    is_available: bool = True
    calls: list[dict[str, object]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.is_available

    def describe(self) -> str:
        return "fake installer"

    async def install(
        self,
        specs: Sequence[str],
        target: Path,
        *,
        extra_args: Sequence[str] = (),
        on_line: LineSink | None = None,
    ) -> int:
        if not self.is_available:
            raise ExtrasUnavailableError("fake installer unavailable")
        self.calls.append(
            {
                "specs": list(specs),
                "target": target,
                "extra_args": list(extra_args),
            }
        )
        for spec in specs:
            name = self.packages.get(spec)
            if name:
                (target / name).mkdir(parents=True, exist_ok=True)
                (target / name / "__init__.py").write_text("VERSION = 'fake'\n")
            if on_line is not None:
                on_line(f"installed {spec}")
        return self.returncode


__all__ = ["FakeExtrasInstaller"]
