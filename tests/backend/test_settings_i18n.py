"""守卫：每个 SettingSpec 的 label_key 都在前端文案表（zh 与 en）里有对应条目。

节点面板与偏好窗口直接按 schema 渲染，标签缺失时界面会显示原始键名。
"""
from __future__ import annotations

import re
from pathlib import Path

from kacore.settings.schema import SPECS

I18N_DESKTOP = Path(__file__).resolve().parents[2] / "web" / "frontend" / "lib" / "i18nDesktop.ts"


def _table(source: str, name: str) -> set[str]:
    start = source.index(f"export const {name}")
    body = source[start : source.index("\n};", start)]
    return set(re.findall(r"^\s+([a-z0-9_]+):", body, re.M))


def test_every_setting_has_zh_and_en_label() -> None:
    source = I18N_DESKTOP.read_text(encoding="utf-8")
    zh, en = _table(source, "DESKTOP_ZH"), _table(source, "DESKTOP_EN")
    labels = {spec.label_key for spec in SPECS}
    assert labels - zh == set()
    assert labels - en == set()
