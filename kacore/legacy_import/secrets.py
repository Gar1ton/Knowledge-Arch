"""读取旧版插件的加密机密库（只读，不改动旧文件）。

旧版与新版用同一种格式：每个机密一个 `<name>.secret`（Fernet 密文），主密钥在同目录的
`master.key`。旧版没有系统钥匙串，主密钥总在文件里；缺失或损坏时对应机密无法解密，
由调用方提示用户重新填写。
"""

from __future__ import annotations

import re
from pathlib import Path

_SECRET_SUFFIX = ".secret"
_NAME_RE = re.compile(r"^[a-z0-9_.-]+$")


def read_legacy_secrets(secrets_dir: Path) -> dict[str, str]:
    """返回 {机密名: 明文}；读不了的条目跳过（不抛错）。"""
    from cryptography.fernet import Fernet, InvalidToken

    key_path = Path(secrets_dir) / "master.key"
    if not key_path.is_file():
        return {}
    try:
        fernet = Fernet(key_path.read_bytes().strip())
    except (OSError, ValueError):
        return {}
    values: dict[str, str] = {}
    for item in sorted(Path(secrets_dir).glob(f"*{_SECRET_SUFFIX}")):
        name = item.name[: -len(_SECRET_SUFFIX)]
        if not _NAME_RE.match(name):
            continue
        try:
            value = fernet.decrypt(item.read_bytes()).decode("utf-8").strip()
        except (OSError, InvalidToken, UnicodeDecodeError):
            continue
        if value:
            values[name] = value
    return values


__all__ = ["read_legacy_secrets"]
