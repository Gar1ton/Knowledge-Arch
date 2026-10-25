"""旧插件配置 → 新设置键。

输入是旧插件配置 dict 与 `runtime_config.json` 覆盖合并后的结果（与插件版组合根同口径）。
取值一律经既有 `Config` 的 getter 读出，旧键回退（如 vector_db.embedding_model）与钳制规则
自动沿用；只翻译「原配置里显式出现过」的键，其余保持新版默认值。

- RENAMED：按 SettingSpec.target 反查，(section, key) → 新键（schema 是唯一真相源）。
- TO_PROVIDER：一组 地址 + 模型 (+ 密钥) → 一个 ProviderProfile + 一个 ModelRef：
  deep_thinking.llm_* → flow.models.research；graph.lightrag_llm_* → flow.models.graph；
  external embedding → flow.embedding.source。
- 两个 device 合并为 prefs.local_models.device（rerank 仍在设备不可用时静默回退）。
- 其余键（Web 控制台、对话开关、MCP server 名、已移除的 embedding 来源等）丢弃并列入 dropped。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from kacore.config import (
    EMBEDDING_PROVIDERS,
    ENV_DEEP_THINKING_LLM_API_KEY,
    ENV_EMBEDDING_API_KEY,
    ENV_LIGHTRAG_LLM_API_KEY,
    ENV_R2_SECRET_ACCESS_KEY,
    Config,
)
from kacore.providers.anthropic import DEFAULT_BASE_URL as ANTHROPIC_BASE_URL
from kacore.providers.base import (
    KIND_ANTHROPIC,
    KIND_OPENAI_COMPATIBLE,
    ProviderProfile,
    api_key_ref_for,
)
from kacore.providers.presets import PRESETS
from kacore.settings.base import SettingsValidationError
from kacore.settings.schema import SPECS_BY_TARGET, spec_for
from kacore.settings.store import normalize_value

if TYPE_CHECKING:
    from kacore.settings.store import SettingsStore

_GETTERS = {
    "source_store": "get_source_store_config",
    "zotero_sync": "get_zotero_sync_config",
    "embedding": "get_embedding_config",
    "vector_db": "get_vector_db_config",
    "graph": "get_graph_config",
    "rerank": "get_rerank_config",
    "enhanced_recall": "get_enhanced_recall_config",
    "deep_thinking": "get_deep_thinking_config",
    "ask": "get_ask_agent_config",
    "notion_sync": "get_notion_sync_config",
    "r2_sync": "get_r2_sync_config",
}
# 这些键按「原始值」翻译：Config getter 会把它们解析成运行期结果（如 rerank auto → 是否装了模型）。
_RAW_VALUE_KEYS = frozenset({("rerank", "provider")})
# 由下方专门逻辑处理、不走通用 RENAMED 的键。
_SPECIAL_KEYS = frozenset({
    ("embedding", "provider"), ("embedding", "model"), ("embedding", "base_url"),
    ("embedding", "device"), ("rerank", "device"),
    ("vector_db", "embedding_provider"), ("vector_db", "embedding_model"),
    ("vector_db", "base_url"),
    ("deep_thinking", "llm_base_url"), ("deep_thinking", "llm_model"),
    ("deep_thinking", "llm_api_key"),
    ("graph", "lightrag_llm_provider"), ("graph", "lightrag_llm_base_url"),
    ("graph", "lightrag_llm_model"),
    ("r2_sync", "access_key_id"), ("r2_sync", "secret_access_key"),
    ("graph", "max_token_size"),
})


@dataclass
class LegacyTranslation:
    values: dict[str, Any] = field(default_factory=dict)
    providers: list[dict[str, Any]] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)


def _sections(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: dict(value) for name, value in raw.items() if isinstance(value, dict)}


def _provider_id_for(base_url: str, existing: set[str]) -> str:
    normalized = base_url.rstrip("/")
    for preset in PRESETS:
        if preset.base_url.rstrip("/") == normalized:
            candidate = preset.id
            break
    else:
        parts = urlsplit(normalized)
        host = (parts.hostname or "custom").lower()
        if parts.port:
            host = f"{host}-{parts.port}"
        candidate = re.sub(r"[^a-z0-9_-]+", "-", host).strip("-") or "custom"
    base, n = candidate, 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


class _ProviderCollector:
    """按 base_url 去重地收集连接。"""

    def __init__(self, result: LegacyTranslation) -> None:
        self._result = result
        self._by_url: dict[str, str] = {}

    def ref(self, base_url: str, model: str, api_key: str, *, purpose: str) -> dict[str, str]:
        url = base_url.strip().rstrip("/")
        provider_id = self._by_url.get(url)
        if provider_id is None:
            provider_id = _provider_id_for(url, {p["id"] for p in self._result.providers})
            kind = KIND_ANTHROPIC if url == ANTHROPIC_BASE_URL else KIND_OPENAI_COMPATIBLE
            label = next((p.label for p in PRESETS if p.id == provider_id), provider_id)
            profile = ProviderProfile(
                id=provider_id,
                kind=kind,
                label=label,
                base_url=url,
                api_key_ref=api_key_ref_for(provider_id),
            )
            self._result.providers.append(profile.to_dict())
            self._by_url[url] = provider_id
        if api_key:
            self._result.secrets[api_key_ref_for(provider_id)] = api_key
        elif api_key_ref_for(provider_id) not in self._result.secrets:
            self._result.attention.append(
                f"{purpose}：连接「{provider_id}」（{url}）需要在「模型供应商」里填写 API Key"
                "（本机服务可留空）"
            )
        return {"provider_id": provider_id, "model": model.strip()}


def translate(raw: Mapping[str, Any]) -> LegacyTranslation:
    """旧配置 → LegacyTranslation；不改动输入，不读写任何文件。"""
    result = LegacyTranslation()
    sections = _sections(raw)
    cfg = Config({name: dict(values) for name, values in sections.items()})
    collector = _ProviderCollector(result)
    consumed: set[tuple[str, str]] = set()

    # ── RENAMED：schema target 反查 ─────────────────────────────────
    for section, values in sections.items():
        getter = _GETTERS.get(section)
        for key, raw_value in values.items():
            spec = SPECS_BY_TARGET.get((section, key))
            if spec is None or (section, key) in _SPECIAL_KEYS or getter is None:
                continue
            value = (
                raw_value
                if (section, key) in _RAW_VALUE_KEYS
                else getattr(getattr(cfg, getter)(), key, raw_value)
            )
            try:
                result.values[spec.key] = normalize_value(spec, value)
                consumed.add((section, key))
            except SettingsValidationError:
                result.attention.append(f"{section}.{key} 的旧值 {raw_value!r} 无效，已恢复默认")
                consumed.add((section, key))

    # ── embedding 来源 ─────────────────────────────────────────────
    emb_raw = sections.get("embedding", {})
    vec_raw = sections.get("vector_db", {})
    embedding_keys = {"provider", "model", "base_url"}
    if embedding_keys & emb_raw.keys() or {"embedding_provider", "embedding_model", "base_url"} & (
        vec_raw.keys()
    ):
        raw_provider = str(
            emb_raw.get("provider", vec_raw.get("embedding_provider", "local"))
        ).strip().lower()
        emb = cfg.get_embedding_config()
        if raw_provider == "external":
            result.values["flow.embedding.source"] = collector.ref(
                emb.base_url, emb.model, os.environ.get(ENV_EMBEDDING_API_KEY, ""),
                purpose="Embedding",
            )
        elif raw_provider in EMBEDDING_PROVIDERS:
            result.values["flow.embedding.local_model"] = emb.model
        else:
            result.attention.append(
                f"原 Embedding 来源 {raw_provider!r} 已移除：改用本地模型，向量索引将重建"
            )
        consumed.update(("embedding", k) for k in embedding_keys)
        consumed.update(
            ("vector_db", k) for k in ("embedding_provider", "embedding_model", "base_url")
        )
    if "max_token_size" not in emb_raw and "max_token_size" in sections.get("graph", {}):
        result.values["flow.embedding.max_token_size"] = cfg.get_embedding_config().max_token_size
        consumed.add(("graph", "max_token_size"))

    # ── 设备：embedding.device 优先，其次 rerank.device ─────────────
    for section in ("embedding", "rerank"):
        device = str(sections.get(section, {}).get("device", "")).strip().lower()
        if not device:
            continue
        consumed.add((section, "device"))
        normalized = "cuda" if device.startswith("cuda") else device
        if "prefs.local_models.device" in result.values:
            continue
        try:
            result.values["prefs.local_models.device"] = normalize_value(
                spec_for("prefs.local_models.device"), normalized
            )
        except SettingsValidationError:
            result.attention.append(f"{section}.device={device!r} 无法识别，已恢复 auto")

    # ── TO_PROVIDER：研究与图谱的独立端点 ─────────────────────────────
    deep = sections.get("deep_thinking", {})
    if str(deep.get("llm_base_url") or "").strip() and str(deep.get("llm_model") or "").strip():
        key = os.environ.get(ENV_DEEP_THINKING_LLM_API_KEY) or str(deep.get("llm_api_key") or "")
        result.values["flow.models.research"] = collector.ref(
            str(deep["llm_base_url"]), str(deep["llm_model"]), key, purpose="研究模型"
        )
    consumed.update(("deep_thinking", k) for k in ("llm_base_url", "llm_model", "llm_api_key"))

    graph = sections.get("graph", {})
    graph_url = str(graph.get("lightrag_llm_base_url") or "").strip()
    graph_model = str(graph.get("lightrag_llm_model") or "").strip()
    graph_provider = str(graph.get("lightrag_llm_provider") or "").strip().lower()
    if graph_provider not in {"main", "local", "api"}:
        # 插件版规则：未声明 provider 但填了完整端点 → 视为 local。
        graph_provider = "local" if graph_url and graph_model else "main"
    if graph_provider in {"local", "api"} and graph_url and graph_model:
        result.values["flow.models.graph"] = collector.ref(
            graph_url,
            graph_model,
            os.environ.get(ENV_LIGHTRAG_LLM_API_KEY, ""),
            purpose="图谱模型",
        )
    if graph:
        consumed.update(
            ("graph", k)
            for k in ("lightrag_llm_provider", "lightrag_llm_base_url", "lightrag_llm_model")
        )

    # ── R2 凭据转入加密库 ──────────────────────────────────────────
    r2 = sections.get("r2_sync", {})
    access_key = str(r2.get("access_key_id") or "").strip()
    secret_key = str(r2.get("secret_access_key") or os.environ.get(ENV_R2_SECRET_ACCESS_KEY) or "")
    if access_key:
        result.secrets["flow.r2.access_key_id"] = access_key
    if secret_key.strip():
        result.secrets["flow.r2.secret_access_key"] = secret_key.strip()
    if r2:
        consumed.update({("r2_sync", "access_key_id"), ("r2_sync", "secret_access_key")})

    # ── 丢弃项报告 ─────────────────────────────────────────────────
    for section, values in sections.items():
        for key in values:
            if (section, key) not in consumed:
                result.dropped.append(f"{section}.{key}")
    for key, value in raw.items():
        if not isinstance(value, dict):
            result.dropped.append(str(key))
    return result


def apply_translation(store: SettingsStore, translation: LegacyTranslation) -> None:
    """把翻译结果写进 SettingsStore：连接 → 密钥 → 设置值（一次落盘的批量写）。"""
    merged = {p["id"]: p for p in store.providers()}
    for profile in translation.providers:
        merged.setdefault(profile["id"], profile)
    store.set_providers(merged.values())
    for name, value in translation.secrets.items():
        if name.startswith("provider."):
            store.secrets.set_secret(name, value)
    store.set_many({
        **translation.values,
        **{name: value for name, value in translation.secrets.items() if name.startswith("flow.")},
    })


__all__ = ["LegacyTranslation", "apply_translation", "translate"]
