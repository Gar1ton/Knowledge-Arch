"""SettingsStore：一份 settings.json，两层归属（flow.* 节点 / prefs.* 偏好）。

文档形状（`version` 便于将来迁移）::

    {"version": 1,
     "values": {"flow.graph.query_mode": "mix",
                "flow.r2.access_key_id": {"ref": "flow.r2.access_key_id"}},
     "providers": [ProviderProfile.to_dict(), ...]}

- 未写入的键取 SettingSpec.default，不落盘（默认值随版本演进时旧文件自动跟上）。
- secret 只在加密库里保存明文，文档里只留 `{"ref": name}`；读取时现取。
- 每次写入都校验 kind / options / bounds，非法值抛 SettingsValidationError（不落盘）。
- `set()` 返回 SettingChange，说明是否真的变了、后果是 none / restart / rebuild。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kacore.providers.base import ModelRef
from kacore.settings.base import SecretStore, SettingsBackend, SettingsValidationError
from kacore.settings.json_file import JsonFileSettingsBackend
from kacore.settings.memory import MemorySecretStore, MemorySettingsBackend
from kacore.settings.schema import (
    CONSEQUENCE_NONE,
    SPECS,
    SettingSpec,
    spec_for,
    specs_for_home,
)

logger = logging.getLogger("SettingsStore")

DOCUMENT_VERSION = 1
_MASK = "••••"


@dataclass(frozen=True)
class SettingChange:
    key: str
    changed: bool
    consequence: str

    @property
    def effective_consequence(self) -> str:
        """没变就没有后果（重存同一个值不触发重启 / 重建）。"""
        return self.consequence if self.changed else CONSEQUENCE_NONE


def mask_secret(value: str) -> str:
    """界面只显示后 4 位；短值整体打码。"""
    if not value:
        return ""
    if len(value) <= 8:
        return _MASK
    return f"{_MASK}{value[-4:]}"


def _number(spec: SettingSpec, value: Any, *, integer: bool) -> int | float:
    if isinstance(value, bool):
        raise SettingsValidationError(f"{spec.key} 需要数字")
    try:
        number: int | float = float(value) if not isinstance(value, (int, float)) else value
    except (TypeError, ValueError):
        raise SettingsValidationError(f"{spec.key} 需要数字") from None
    if isinstance(number, float) and not math.isfinite(number):
        raise SettingsValidationError(f"{spec.key} 需要有限数字")
    if integer:
        if float(number) != int(number):
            raise SettingsValidationError(f"{spec.key} 需要整数")
        number = int(number)
    else:
        number = float(number)
    if spec.bounds is not None:
        lo, hi = spec.bounds
        if not lo <= number <= hi:
            raise SettingsValidationError(f"{spec.key} 超出范围 [{lo:g}, {hi:g}]")
    return number


def normalize_value(spec: SettingSpec, value: Any) -> Any:
    """把写入值归一为存储形状；不合法抛 SettingsValidationError。"""
    kind = spec.kind
    if value is None:
        if spec.nullable:
            return None
        raise SettingsValidationError(f"{spec.key} 不能为空")
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{spec.key} 需要布尔值")
        return value
    if kind == "int":
        return _number(spec, value, integer=True)
    if kind == "float":
        return _number(spec, value, integer=False)
    if kind in {"str", "path", "secret"}:
        if not isinstance(value, str):
            raise SettingsValidationError(f"{spec.key} 需要字符串")
        return value.strip()
    if kind == "text":
        if not isinstance(value, str):
            raise SettingsValidationError(f"{spec.key} 需要字符串")
        return value
    if kind == "enum":
        if value not in spec.options:
            raise SettingsValidationError(
                f"{spec.key} 只能是 {' / '.join(spec.options)}"
            )
        return value
    if kind == "model_ref":
        if isinstance(value, str) and value in spec.options:
            return value
        ref = ModelRef.parse(value)
        if ref is None:
            raise SettingsValidationError(f"{spec.key} 需要 {{provider_id, model}}")
        return ref.to_dict()
    raise SettingsValidationError(f"{spec.key} 的类型 {kind} 不受支持")


class SettingsStore:
    """设置的唯一读写入口（组合根构造一次，注入需要它的子系统）。"""

    def __init__(self, backend: SettingsBackend, secrets: SecretStore) -> None:
        self._backend = backend
        self._secrets = secrets
        self._document = self._normalize_document(backend.load())

    # ── 构造 ──────────────────────────────────────────────────────

    @classmethod
    def open(cls, path: Path, secrets: SecretStore) -> SettingsStore:
        return cls(JsonFileSettingsBackend(path), secrets)

    @classmethod
    def in_memory(
        cls,
        values: Mapping[str, Any] | None = None,
        *,
        secrets: SecretStore | None = None,
    ) -> SettingsStore:
        store = cls(MemorySettingsBackend(), secrets or MemorySecretStore())
        if values:
            store.set_many(values)
        return store

    @staticmethod
    def _normalize_document(raw: dict[str, Any]) -> dict[str, Any]:
        values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
        providers = raw.get("providers") if isinstance(raw.get("providers"), list) else []
        return {"version": DOCUMENT_VERSION, "values": dict(values), "providers": list(providers)}

    @property
    def persisted(self) -> bool:
        """后端是否已有 settings 文档（首启迁移判断用）。"""
        return self._backend.exists

    @property
    def secrets(self) -> SecretStore:
        return self._secrets

    def reload(self) -> None:
        """重读持久化文档（服务软重启时调用）。"""
        self._document = self._normalize_document(self._backend.load())

    def save(self) -> None:
        self._backend.save(self._document)

    # ── 读取 ──────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        """返回生效值；secret 返回明文（只供后端内部使用，绝不直接回给界面）。"""
        spec = spec_for(key)
        if spec.kind == "secret":
            return self._secrets.get_secret(spec.secret_name)
        values = self._document["values"]
        if key not in values:
            return spec.default
        return values[key]

    def is_set(self, key: str) -> bool:
        spec = spec_for(key)
        if spec.kind == "secret":
            return self._secrets.has_secret(spec.secret_name)
        return key in self._document["values"]

    def public_value(self, key: str) -> Any:
        spec = spec_for(key)
        if spec.kind == "secret":
            secret = self.get(key)
            return {"set": bool(secret), "masked": mask_secret(secret)}
        return self.get(key)

    def schema(self, home: str | None = None) -> list[dict[str, Any]]:
        """节点面板 / 偏好窗口的渲染数据：spec + 当前值（机密打码）。"""
        return [
            {
                **spec.to_public(),
                "value": self.public_value(spec.key),
                "is_set": self.is_set(spec.key),
            }
            for spec in specs_for_home(home)
        ]

    def values(self, *, include_secrets: bool = False) -> dict[str, Any]:
        return {
            spec.key: self.get(spec.key)
            for spec in SPECS
            if include_secrets or spec.kind != "secret"
        }

    # ── 写入 ──────────────────────────────────────────────────────

    def _apply(self, key: str, value: Any) -> SettingChange:
        spec = spec_for(key)
        normalized = normalize_value(spec, value)
        before = self.get(key)
        if spec.kind == "secret":
            if normalized:
                self._secrets.set_secret(spec.secret_name, normalized)
                self._document["values"][key] = {"ref": spec.secret_name}
            else:
                self._secrets.delete_secret(spec.secret_name)
                self._document["values"].pop(key, None)
        else:
            self._document["values"][key] = normalized
        return SettingChange(key, before != normalized, spec.consequence)

    def set(self, key: str, value: Any) -> SettingChange:
        change = self._apply(key, value)
        self.save()
        return change

    def set_many(self, values: Mapping[str, Any]) -> list[SettingChange]:
        """先校验全部值再写入：任一项非法时文档与加密库都不变。"""
        for key, value in values.items():
            normalize_value(spec_for(key), value)
        changes = [self._apply(key, value) for key, value in values.items()]
        self.save()
        return changes

    def reset(self, key: str) -> SettingChange:
        """恢复默认（删除显式值）；secret 等同清空。"""
        spec = spec_for(key)
        before = self.get(key)
        if spec.kind == "secret":
            self._secrets.delete_secret(spec.secret_name)
        self._document["values"].pop(key, None)
        self.save()
        return SettingChange(key, before != self.get(key), spec.consequence)

    # ── 连接列表（ProviderRegistry 独占读写）──────────────────────────

    def providers(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._document["providers"] if isinstance(p, dict)]

    def set_providers(self, providers: Iterable[Mapping[str, Any]]) -> None:
        self._document["providers"] = [dict(p) for p in providers]
        self.save()

    # ── 跨设备携带（R2 快照）────────────────────────────────────────

    def portable_document(self) -> dict[str, Any]:
        """只含 portable 的非机密显式值与连接（不含密钥）。"""
        values = {
            spec.key: self._document["values"][spec.key]
            for spec in SPECS
            if spec.portable and spec.kind != "secret" and spec.key in self._document["values"]
        }
        providers = [{**p, "models": list(p.get("models") or [])} for p in self.providers()]
        return {"version": DOCUMENT_VERSION, "values": values, "providers": providers}

    def merge_portable(self, portable: Mapping[str, Any]) -> list[str]:
        """恢复快照时合并可携设置；返回被忽略的键（未知 / 不可携 / 非法）。"""
        ignored: list[str] = []
        raw_values = portable.get("values") if isinstance(portable.get("values"), dict) else {}
        for key, value in raw_values.items():
            try:
                spec = spec_for(key)
                if not spec.portable or spec.kind == "secret":
                    raise KeyError(key)
                self._document["values"][key] = normalize_value(spec, value)
            except (KeyError, SettingsValidationError):
                ignored.append(key)
        incoming = portable.get("providers")
        if isinstance(incoming, list):
            merged = {p.get("id"): p for p in self.providers()}
            for profile in incoming:
                if isinstance(profile, dict) and profile.get("id"):
                    merged[profile["id"]] = {**merged.get(profile["id"], {}), **profile}
            self._document["providers"] = list(merged.values())
        self.save()
        return ignored

    # ── 与既有 Config 的桥 ──────────────────────────────────────────

    def to_config_raw(self) -> dict[str, Any]:
        from kacore.settings.bridge import settings_to_config_raw

        return settings_to_config_raw(self)


__all__ = ["DOCUMENT_VERSION", "SettingChange", "SettingsStore", "mask_secret", "normalize_value"]
