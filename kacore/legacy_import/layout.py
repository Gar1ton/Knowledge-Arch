"""旧版插件安装的目录布局与定位。

用户在欢迎页选的是「旧安装的根目录」（宿主程序目录）；也接受直接选 `data/` 或插件数据目录本身。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 旧插件在宿主里的注册名：数据目录名与配置文件名都由它派生。
PLUGIN_NAME = "astrbot_plugin_knowledge_repository"
PLUGIN_DATA_PARTS = ("data", "plugin_data", PLUGIN_NAME)
PLUGIN_CONFIG_PARTS = ("data", "config", f"{PLUGIN_NAME}_config.json")
RUNTIME_CONFIG_FILE = "runtime_config.json"
# 数据库里绝对路径的「插件数据目录」特征段（改写前缀用；正反斜杠都认）。
PATH_MARKER = f"/plugin_data/{PLUGIN_NAME}"


class LegacyImportError(RuntimeError):
    """导入不可进行或失败；message 面向用户（不含旧宿主名）。"""


@dataclass(frozen=True)
class LegacyInstall:
    """定位到的一份旧版插件数据。"""

    data_dir: Path
    config_file: Path | None

    @property
    def runtime_config(self) -> Path | None:
        path = self.data_dir / RUNTIME_CONFIG_FILE
        return path if path.is_file() else None


def locate(selected: str | Path) -> LegacyInstall:
    """从用户选的目录找到旧插件数据目录与配置文件；找不到抛 LegacyImportError。"""
    root = Path(selected).expanduser()
    if not root.is_dir():
        raise LegacyImportError("所选位置不是一个文件夹。")
    root = root.resolve()
    candidates = [
        root.joinpath(*PLUGIN_DATA_PARTS),
        root.joinpath(*PLUGIN_DATA_PARTS[1:]),  # 选的是 data/
        root if root.name == PLUGIN_NAME else None,  # 选的是插件数据目录本身
    ]
    data_dir = next((c for c in candidates if c is not None and c.is_dir()), None)
    if data_dir is None:
        raise LegacyImportError(
            "没有在所选文件夹里找到旧版插件数据：请选择旧版程序的安装根目录"
            "（其中应有 data/plugin_data/ 子目录）。"
        )
    host_root = data_dir.parent.parent.parent
    config = host_root.joinpath(*PLUGIN_CONFIG_PARTS)
    return LegacyInstall(data_dir=data_dir, config_file=config if config.is_file() else None)


__all__ = [
    "PATH_MARKER",
    "PLUGIN_CONFIG_PARTS",
    "PLUGIN_DATA_PARTS",
    "PLUGIN_NAME",
    "LegacyImportError",
    "LegacyInstall",
    "locate",
]
