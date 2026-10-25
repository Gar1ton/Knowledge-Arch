"""应用目录解析（shell 层工具）。

三平台标准目录由 platformdirs 给出：
- macOS   ~/Library/Application Support/Knowledge Arch（日志 ~/Library/Logs/Knowledge Arch）
- Windows %LOCALAPPDATA%\\Knowledge Arch（日志在其下 Logs/）
- Linux   ~/.local/share/Knowledge Arch（日志 ~/.local/state/Knowledge Arch/log）
`--data-dir` 显式给出时，数据与日志都放在该目录下（便携 / 开发 / 测试）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import platformdirs

from ka_server import APP_NAME


@dataclass(frozen=True)
class AppPaths:
    """一次启动使用的全部目录。data 下的布局与插件版数据目录一致（同一 schema）。"""

    data: Path
    logs: Path

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    @property
    def secrets(self) -> Path:
        return self.data / "secrets"

    @property
    def uploads(self) -> Path:
        return self.data / "uploads"

    @property
    def extras(self) -> Path:
        return self.data / "extras"

    @property
    def lock_file(self) -> Path:
        return self.data / ".ka_server.lock"

    @classmethod
    def resolve(cls, data_dir: str | Path | None = None) -> AppPaths:
        """data_dir 为空时用平台标准目录；否则数据与日志都落在 data_dir 下。"""
        if data_dir:
            root = Path(data_dir).expanduser().resolve()
            return cls(data=root, logs=root / "logs")
        return cls(
            data=Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)),
            logs=Path(platformdirs.user_log_dir(APP_NAME, appauthor=False)),
        )

    def ensure(self) -> None:
        """创建本次启动需要的目录（幂等）。"""
        for path in (self.data, self.logs, self.uploads):
            path.mkdir(parents=True, exist_ok=True)


__all__ = ["AppPaths"]
