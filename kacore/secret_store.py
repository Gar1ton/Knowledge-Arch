"""本机加密机密库（API Key、R2 凭据、Notion token 等）。

每个机密一个 Fernet 密文文件；主密钥优先放在系统钥匙串（macOS Keychain / Windows 凭据管理器 /
Linux Secret Service），没有可用钥匙串时回退到数据目录里的 master.key（插件版的做法）。
已有 master.key 且钥匙串可用时自动迁入钥匙串，回读校验通过后才删除文件。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

_SECRET_NAME_RE = re.compile(r"^[a-z0-9_.-]+$")
KEYRING_SERVICE = "Knowledge Arch"
logger = logging.getLogger("EncryptedSecretStore")


class KeyringBackend(Protocol):
    """`keyring` 模块的最小子集（便于测试注入）。"""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


def system_keyring() -> KeyringBackend | None:
    """返回可用的系统钥匙串；没有（如无桌面会话的 Linux / WSL）时返回 None。"""
    try:
        import keyring
        from keyring.backends import fail
    except Exception:  # noqa: BLE001 - 可选依赖，缺失即回退文件
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 0) <= 0:
        return None
    return keyring  # type: ignore[return-value]


class EncryptedSecretStore:
    """Fernet 加密的机密库，作用域为一个数据目录。

    防的是「明文密钥进配置 / 日志 / API 响应」与静态拷贝泄露；不防拥有当前用户全部权限的进程。
    主密钥在钥匙串里时，单独拷走数据目录（含 R2 快照）也解不开密文——换机恢复后需重新填写密钥。
    """

    def __init__(self, root: Path, *, keyring_backend: Any | None = None) -> None:
        self._root = Path(root)
        self._key_path = self._root / "master.key"
        self._keyring = keyring_backend
        # 同一台机器可能有多个数据目录：钥匙串条目按目录区分。
        digest = hashlib.sha256(str(self._root.resolve()).encode("utf-8")).hexdigest()[:16]
        self._keyring_user = f"master-key:{digest}"
        self._key: bytes | None = None

    @property
    def uses_keyring(self) -> bool:
        return self._keyring is not None and self._load_keyring_key() is not None

    def get_secret(self, name: str) -> str:
        path = self._secret_path(name)
        if not path.exists():
            return ""
        try:
            token = path.read_bytes()
            return self._fernet().decrypt(token).decode("utf-8")
        except (OSError, InvalidToken, UnicodeDecodeError):
            return ""

    def set_secret(self, name: str, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            self.delete_secret(name)
            return
        self._root.mkdir(parents=True, exist_ok=True)
        token = self._fernet().encrypt(cleaned.encode("utf-8"))
        path = self._secret_path(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(token)
        _chmod_private(tmp)
        tmp.replace(path)
        _chmod_private(path)

    def delete_secret(self, name: str) -> None:
        try:
            self._secret_path(name).unlink()
        except FileNotFoundError:
            return

    def has_secret(self, name: str) -> bool:
        return bool(self.get_secret(name))

    def masked_secret(self, name: str) -> str:
        return _mask(self.get_secret(name))

    def _load_keyring_key(self) -> bytes | None:
        if self._keyring is None:
            return None
        try:
            value = self._keyring.get_password(KEYRING_SERVICE, self._keyring_user)
        except Exception as exc:  # noqa: BLE001 - 钥匙串不可用时回退文件
            logger.warning((
                "Reading the system keychain failed; falling back to the file master key: %s"
            ), type(exc).__name__)
            self._keyring = None
            return None
        return value.encode("ascii") if value else None

    def _store_keyring_key(self, key: bytes) -> bool:
        """写入钥匙串并回读校验；任何失败都返回 False（调用方保留文件）。"""
        if self._keyring is None:
            return False
        try:
            self._keyring.set_password(KEYRING_SERVICE, self._keyring_user, key.decode("ascii"))
        except Exception as exc:  # noqa: BLE001
            logger.warning((
                "Writing to the system keychain failed; keeping the file master key: %s"
            ), type(exc).__name__)
            self._keyring = None
            return False
        return self._load_keyring_key() == key

    def _master_key(self) -> bytes:
        if self._key is not None:
            return self._key
        key = self._load_keyring_key()
        if key is None and self._key_path.exists():
            key = self._key_path.read_bytes().strip()
            if self._store_keyring_key(key):
                self._key_path.unlink()
                logger.info("Master key moved into the system keychain")
        if key is None:
            key = Fernet.generate_key()
            if not self._store_keyring_key(key):
                self._root.mkdir(parents=True, exist_ok=True)
                self._key_path.write_bytes(key)
                _chmod_private(self._key_path)
        self._key = key
        return key

    def _fernet(self) -> Fernet:
        self._root.mkdir(parents=True, exist_ok=True)
        return Fernet(self._master_key())

    def _secret_path(self, name: str) -> Path:
        if not _SECRET_NAME_RE.match(name):
            raise ValueError(f"invalid secret name: {name!r}")
        return self._root / f"{name}.secret"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-2:]}"


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


__all__ = ["KEYRING_SERVICE", "EncryptedSecretStore", "KeyringBackend", "system_keyring"]
