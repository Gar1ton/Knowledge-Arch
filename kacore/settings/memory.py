"""设置与机密的内存实现（无 I/O，供接口对换测试）。"""

from __future__ import annotations

import copy
from typing import Any

from kacore.settings.base import SettingsBackend


class MemorySettingsBackend(SettingsBackend):
    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self._document = copy.deepcopy(document) if document is not None else None

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self._document) if self._document is not None else {}

    def save(self, document: dict[str, Any]) -> None:
        self._document = copy.deepcopy(document)

    @property
    def exists(self) -> bool:
        return self._document is not None


class MemorySecretStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get_secret(self, name: str) -> str:
        return self._values.get(name, "")

    def set_secret(self, name: str, value: str) -> None:
        cleaned = value.strip()
        if cleaned:
            self._values[name] = cleaned
        else:
            self._values.pop(name, None)

    def delete_secret(self, name: str) -> None:
        self._values.pop(name, None)

    def has_secret(self, name: str) -> bool:
        return bool(self._values.get(name))


__all__ = ["MemorySecretStore", "MemorySettingsBackend"]
