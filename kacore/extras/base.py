"""可选组件安装器接口（接口先行，见 ../../ARCHITECTURE.md §4）。

安装包自带的 Python 运行时只读，可选组件不能装进它的 site-packages；ExtrasManager 把每个组件
装进数据目录下自己的 `extras/<pack>/` 目录，启动时再把这些目录加进 `sys.path`。
本文件只定义「把一组规格装进指定目录」的最小契约；生产实现是 `uv.py`，测试替身是 `memory.py`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from pathlib import Path

LineSink = Callable[[str], None]


class ExtrasUnavailableError(RuntimeError):
    """安装器本身不可用（如找不到 uv 可执行文件）；message 面向用户。"""


class ExtrasInstaller(ABC):
    """把一组包规格安装进一个空目录。

    契约：
    - target 由调用方创建并保证为空；安装器只往里面写，不碰其他目录，也不改当前进程的 sys.path。
    - 输出逐行交给 on_line（不含换行符）；返回进程退出码，0 = 成功。
    - 安装器不可用时抛 ExtrasUnavailableError（不返回退出码）。
    """

    @property
    @abstractmethod
    def available(self) -> bool:
        """安装器是否可用（界面据此禁用安装按钮）。"""

    @abstractmethod
    def describe(self) -> str:
        """给诊断页看的一行说明（例如 uv 的路径）。"""

    @abstractmethod
    async def install(
        self,
        specs: Sequence[str],
        target: Path,
        *,
        extra_args: Sequence[str] = (),
        on_line: LineSink | None = None,
    ) -> int:
        """安装 specs 到 target；extra_args 原样交给安装器（如 torch 的构建选择）。"""


__all__ = ["ExtrasInstaller", "ExtrasUnavailableError", "LineSink"]
