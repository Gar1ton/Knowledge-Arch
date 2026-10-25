"""Knowledge Arch 本地后端的进程薄壳（shell 层，见 ../ARCHITECTURE.md §2）。

由桌面壳以 `python -I -m ka_server serve --port 0` 启动：解析数据目录、装配组合根、
在 127.0.0.1 上起 HTTP 服务、打印 `KA_READY port=…`，收到退出信号后有序拆除。
这里只做进程生命周期，不写任何业务。
"""

from __future__ import annotations

APP_NAME = "Knowledge Arch"
APP_VERSION = "2.0.0-dev"

__all__ = ["APP_NAME", "APP_VERSION"]
