"""用口令加密一组密钥（utils·3.0.0 导出导入）。

为何存在：本机的密钥库（`kacore/secret_store.py`）用系统钥匙串里的主密钥加密，
拷走数据目录或换机后解不开，密钥都要重填。导出 `.kaarch` 时把密钥解出来，用用户设的口令
重新加密放进文件；新电脑导入时输入同一个口令即可。不设口令就不带密钥（由调用方决定）。

做法：scrypt 从口令推出 32 字节密钥，AES-GCM 加密 JSON。参数写在盒子里，解密照盒子里的
参数算，以后调整参数也能读旧文件。口令错或文件被改过时 AES-GCM 校验失败，
抛 WrongPassphrase，不返回半截数据。
"""
from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

KDF_NAME = "scrypt"
# n=2^17, r=8：每次推导约 128 MiB 内存、1 秒左右（OWASP 对 scrypt 的建议量级；
# 只在导出 / 导入时算一次）。
SCRYPT_N = 1 << 17
SCRYPT_R = 8
SCRYPT_P = 1
_AAD = b"kaarch-secrets-v1"


class WrongPassphrase(ValueError):
    """口令不对（或文件被改过）。"""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def seal(values: Mapping[str, str], passphrase: str, *, n: int = SCRYPT_N) -> dict[str, object]:
    """加密 {名字: 值}。口令为空抛 ValueError。"""
    if not passphrase:
        raise ValueError("a passphrase is required to export keys")
    salt, nonce = os.urandom(16), os.urandom(12)
    data = AESGCM(_key(passphrase, salt, n, SCRYPT_R, SCRYPT_P)).encrypt(
        nonce, json.dumps(dict(values), ensure_ascii=False).encode("utf-8"), _AAD)
    return {"kdf": KDF_NAME, "n": n, "r": SCRYPT_R, "p": SCRYPT_P, "salt": _b64(salt),
            "nonce": _b64(nonce), "data": _b64(data)}


def open_box(box: Mapping[str, object], passphrase: str) -> dict[str, str]:
    """解密 `seal` 的结果。口令不对抛 WrongPassphrase；盒子格式不对抛 ValueError。"""
    if box.get("kdf") != KDF_NAME:
        raise ValueError("unknown key box format")
    try:
        salt = base64.b64decode(str(box["salt"]))
        nonce = base64.b64decode(str(box["nonce"]))
        data = base64.b64decode(str(box["data"]))
        n, r, p = int(str(box["n"])), int(str(box["r"])), int(str(box["p"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("the key box is damaged") from exc
    try:
        plain = AESGCM(_key(passphrase, salt, n, r, p)).decrypt(nonce, data, _AAD)
    except InvalidTag as exc:
        raise WrongPassphrase("wrong passphrase") from exc
    values = json.loads(plain.decode("utf-8"))
    return {str(k): str(v) for k, v in values.items()}


__all__ = ["WrongPassphrase", "open_box", "seal"]
