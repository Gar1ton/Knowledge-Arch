"""AI 助手接入（shell 层）：独立的 agent token 与连接文件。

「偏好设置 › 通用 › 允许 AI 助手连接」（`prefs.general.agent_access`）打开时，本进程把端口与一个
与界面 token 无关的 agent token 写进连接文件；skill 客户端（Codex / Claude Code 里运行）每次调用都
重新读取，所以后端重启换 token 也不需要重新登记。agent token 能访问的接口由 `web/agent_routes.py`
的白名单限定，且不能写入任何机密。

连接文件仅当前用户可读写（POSIX 0600；Windows 落在当前用户的配置目录），开关关闭或进程退出即删除：

- 默认 `<系统配置目录>/Knowledge Arch/agent-connection.json`（与桌面壳的 shell.json 同目录）；
- `--data-dir` 显式给出时（开发 / 便携）放在该数据目录下，避免与已安装的应用互相覆盖。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

import platformdirs

from ka_server import APP_NAME, APP_VERSION

logger = logging.getLogger("ka_server.agent")

CONNECTION_FILE_NAME = "agent-connection.json"
CONNECTION_VERSION = 1
# 最近一次 agent 请求在这个时间内，界面显示「AI 助手已连接」。
CONNECTED_WINDOW_SECONDS = 120.0


def connection_file_for(data_dir_override: str | None, data_dir: Path) -> Path:
    if data_dir_override:
        return data_dir / CONNECTION_FILE_NAME
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / CONNECTION_FILE_NAME


class AgentAccess:
    """一次进程生命周期内的 agent 接入状态；token 只在内存与连接文件里。"""

    def __init__(self, connection_file: Path, *, token: str | None = None) -> None:
        self.connection_file = connection_file
        self.token = token or secrets.token_urlsafe(32)
        self.port = 0
        self.enabled = False
        self.last_seen = 0.0

    def start(self, port: int, enabled: bool) -> None:
        """HTTP 起来、端口已知后调用。"""
        self.port = port
        self.set_enabled(enabled)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled and self.port:
            self._write()
        else:
            self.remove()

    def accepts(self, token: str) -> bool:
        return self.enabled and bool(token) and secrets.compare_digest(token, self.token)

    def touch(self) -> None:
        self.last_seen = time.time()

    def status(self) -> dict[str, object]:
        connected = self.enabled and (time.time() - self.last_seen) < CONNECTED_WINDOW_SECONDS
        return {
            "enabled": self.enabled,
            "connected": connected,
            "last_seen_at": self.last_seen or None,
            "connection_file": str(self.connection_file),
        }

    def remove(self) -> None:
        try:
            self.connection_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove the agent connection file: %s", exc)

    def _write(self) -> None:
        """原子写入：先以 0600 新建临时文件再替换，避免出现短暂的宽权限窗口。"""
        payload = {
            "version": CONNECTION_VERSION,
            "url": f"http://127.0.0.1:{self.port}",
            "token": self.token,
            "pid": os.getpid(),
            "app_version": APP_VERSION,
        }
        target = self.connection_file
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.write("\n")
            os.replace(temp, target)
        except OSError:
            temp.unlink(missing_ok=True)
            raise
        logger.info("agent access enabled; connection file written")


__all__ = ["AgentAccess", "CONNECTION_FILE_NAME", "connection_file_for"]
