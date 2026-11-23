"""内置工具清单与运行时工具箱（adapters 层；由组合根注入到 distributor 与 api）。

`BUILTIN` 是唯一列出具体工具的地方。设置项（开关与各工具声明的设置）由 `tool_setting_specs`
按这里的声明生成，所以设置页、工具面板、distributor 都不写任何工具名。

`ToolBox` 契约：
- 每次使用时现读设置（换开关或 key 不用重启）；某工具的设置变了才重建它（其余工具的运行状态保留）。
- `specs()` 只列出「已开启且必填设置齐全」的工具，这就是 distributor 交给模型的工具目录。
- `call(name, arguments)` 从不抛异常：未知 / 未开启的工具、参数错误、网络错误都变成 is_error 的
  结果交给模型；返回的块已按 max_result_chars 截断（账本存的就是模型看到的）。
- `status()` 给工具面板：开关、是否就绪、缺哪些设置、最近成功 / 错误，以及工具自己的运行信息。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from kacore.adapters.tools.arxiv import ArxivSearch
from kacore.adapters.tools.base import Tool, ToolEnv
from kacore.adapters.tools.crossref import CrossrefSearch
from kacore.adapters.tools.openalex import OpenAlexSearch
from kacore.adapters.tools.openlibrary import OpenLibrarySearch
from kacore.adapters.tools.read_open_pdf import ReadOpenPdf
from kacore.adapters.tools.semantic_scholar import SemanticScholarSearch
from kacore.domain.tool_calls import SETTING_SECRET, ToolResult, ToolSpec, clip_blocks

if TYPE_CHECKING:
    from kacore.config import ToolsConfig

logger = logging.getLogger("ToolBox")

BUILTIN: tuple[type[Tool], ...] = (
    CrossrefSearch,
    OpenAlexSearch,
    ArxivSearch,
    SemanticScholarSearch,
    OpenLibrarySearch,
    ReadOpenPdf,
)

SETTINGS_HOME = "flow.external.tools"


def builtin_specs() -> list[ToolSpec]:
    return [cls.spec for cls in BUILTIN]


def setting_key(tool: str, name: str) -> str:
    """设置项的键：`flow.external.tools.<工具>.<设置>`（开关的设置名是 enabled）。"""
    return f"{SETTINGS_HOME}.{tool}.{name}"


def secret_ref(tool: str, name: str) -> str:
    """机密设置在加密库里的条目名。"""
    return f"tools.{tool}.{name}"


@dataclass
class _Health:
    last_ok_at: str = ""
    last_error: str = ""
    last_error_at: str = ""


class ToolBox:
    def __init__(
        self,
        *,
        config_provider: Callable[[], ToolsConfig],
        read_secret: Callable[[str], str],
        env: ToolEnv,
        tools: tuple[type[Tool], ...] = BUILTIN,
    ) -> None:
        self._config = config_provider
        self._read_secret = read_secret
        self._env = env
        self._classes = {cls.spec.name: cls for cls in tools}
        self._built: dict[str, tuple[tuple[Any, ...], Tool]] = {}
        self._health: dict[str, _Health] = {}

    # ── 目录 ────────────────────────────────────────────────

    def specs(self) -> list[ToolSpec]:
        cfg = self._config()
        return [cls.spec for name, cls in self._classes.items()
                if self._enabled(cls.spec, cfg) and not self._missing(cls.spec, cfg)]

    def status(self) -> list[dict[str, Any]]:
        cfg = self._config()
        out = []
        for name, cls in self._classes.items():
            spec = cls.spec
            missing = self._missing(spec, cfg)
            health = self._health.get(name, _Health())
            built = self._built.get(name)
            out.append({
                "name": name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "enabled": self._enabled(spec, cfg),
                "ready": self._enabled(spec, cfg) and not missing,
                "missing": missing,
                "settings": [{"name": s.name, "kind": s.kind, "required": s.required,
                              "key": setting_key(name, s.name)} for s in spec.settings],
                "enabled_key": setting_key(name, "enabled"),
                "last_ok_at": health.last_ok_at,
                "last_error": health.last_error,
                "last_error_at": health.last_error_at,
                "info": built[1].status() if built else {},
            })
        return out

    # ── 调用 ────────────────────────────────────────────────

    async def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        cfg = self._config()
        cls = self._classes.get(name)
        if cls is None or not self._enabled(cls.spec, cfg):
            return ToolResult.error(f"Unknown or disabled tool: {name}")
        missing = self._missing(cls.spec, cfg)
        if missing:
            return ToolResult.error(f"{name} needs these settings first: {', '.join(missing)}")
        health = self._health.setdefault(name, _Health())
        try:
            result = await self._tool(cls, cfg).call(dict(arguments or {}))
        except Exception as exc:  # noqa: BLE001 - 任何失败都交给模型，由它换工具或换参数
            logger.info("Tool %s failed: %s", name, exc)
            health.last_error, health.last_error_at = str(exc)[:300], _now()
            return ToolResult.error(f"{name} failed: {exc}")
        if result.is_error:
            health.last_error, health.last_error_at = result.blocks[0].text[:300], _now()
        else:
            health.last_ok_at = _now()
        return ToolResult(clip_blocks(result.blocks, cfg.max_result_chars), result.is_error)

    # ── 内部 ────────────────────────────────────────────────

    def _enabled(self, spec: ToolSpec, cfg: ToolsConfig) -> bool:
        return cfg.enabled.get(spec.name, spec.default_enabled)

    def _values(self, spec: ToolSpec, cfg: ToolsConfig) -> dict[str, Any]:
        given = cfg.values.get(spec.name, {})
        values: dict[str, Any] = {}
        for setting in spec.settings:
            if setting.kind == SETTING_SECRET:
                values[setting.name] = self._secret(secret_ref(spec.name, setting.name))
            else:
                value = given.get(setting.name)
                values[setting.name] = setting.default if value in (None, "") else value
        return values

    def _missing(self, spec: ToolSpec, cfg: ToolsConfig) -> list[str]:
        values = self._values(spec, cfg)
        return [s.name for s in spec.settings if s.required and values.get(s.name) in (None, "")]

    def _tool(self, cls: type[Tool], cfg: ToolsConfig) -> Tool:
        name = cls.spec.name
        values = self._values(cls.spec, cfg)
        key = tuple(sorted((k, str(v)) for k, v in values.items()))
        built = self._built.get(name)
        if built is None or built[0] != key:
            built = (key, cls.from_settings(
                {k: ("" if v is None else v) for k, v in values.items()}, self._env))
            self._built[name] = built
        return built[1]

    def _secret(self, ref: str) -> str:
        try:
            return (self._read_secret(ref) or "").strip()
        except Exception:  # noqa: BLE001 - 机密库不可用时按「没填」处理
            return ""


def _now() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).isoformat(timespec="seconds")


__all__ = ["BUILTIN", "SETTINGS_HOME", "ToolBox", "builtin_specs", "secret_ref", "setting_key"]
