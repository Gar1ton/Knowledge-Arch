"""设置持久化接口（接口先行）。

`SettingsBackend` 只负责「整份文档」的读写；校验、默认值、机密分流都在 `SettingsStore`。
`SecretStore` 是加密机密库的最小契约：生产实现为 `kacore.secret_store.EncryptedSecretStore`，
测试用 `kacore.settings.memory.MemorySecretStore`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class SettingsValidationError(ValueError):
    """写入值不符合 SettingSpec（类型 / 取值 / 范围）。message 面向用户。"""


class SecretStore(Protocol):
    """机密的读写；名称只含小写字母数字与 . _ -，空值等同删除。"""

    def get_secret(self, name: str) -> str: ...

    def set_secret(self, name: str, value: str) -> None: ...

    def delete_secret(self, name: str) -> None: ...

    def has_secret(self, name: str) -> bool: ...


class SettingsBackend(ABC):
    """settings 文档的存取。

    契约：load() 在文件不存在时返回空 dict（不抛错）；save() 必须原子替换，
    写一半崩溃不能留下截断文件。
    """

    @abstractmethod
    def load(self) -> dict[str, Any]: ...

    @abstractmethod
    def save(self, document: dict[str, Any]) -> None: ...

    @property
    @abstractmethod
    def exists(self) -> bool:
        """是否已有持久化文档（用于判断是否需要首启迁移）。"""


__all__ = ["SecretStore", "SettingsBackend", "SettingsValidationError"]
