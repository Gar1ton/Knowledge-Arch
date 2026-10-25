"""测试用设置构造：沿用旧式 {section: {key: value}} 写法，经 legacy_keys 翻译成内存 SettingsStore。

让大量既有组合根测试保持可读，同时持续回归旧配置翻译。
"""
from __future__ import annotations

from typing import Any

from kacore.settings.legacy_keys import apply_translation, translate
from kacore.settings.store import SettingsStore


def legacy_settings(raw: dict[str, Any] | None = None) -> SettingsStore:
    store = SettingsStore.in_memory()
    if raw:
        apply_translation(store, translate(raw))
    return store


__all__ = ["legacy_settings"]
