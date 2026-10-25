"""settings.json 的文件实现：UTF-8、排序键、原子替换。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from kacore.settings.base import SettingsBackend

logger = logging.getLogger("SettingsStore")


class JsonFileSettingsBackend(SettingsBackend):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # 读坏文件不应让应用起不来：退回默认值并保留原文件供人工排查。
            logger.error((
                "settings.json cannot be parsed; using the default settings this time: %s"
            ), exc)
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self._path)


__all__ = ["JsonFileSettingsBackend"]
