"""设置与模型供应商 API 子门面（框架无关，`web/` 委派于此）。

- 设置：按 home 取 schema + 当前值（节点面板 / 偏好窗口从这里渲染），按键写入并返回后果。
- 连接：列表 / 预设 / 增删改 / 替换密钥 / 测试连接 / 获取模型列表。

写入后立刻用 SettingsStore 重新桥接出 Config 原始值，同一进程内的后续读取即刻看到新值；
后果为 rebuild 时沿用既有「标记索引不兼容 → 重建」路径，与旧 /api/config/update 一致。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kacore.providers.base import KIND_OPENAI_COMPATIBLE, PROVIDER_KINDS, ModelRef, ProviderError
from kacore.providers.presets import preset_dicts
from kacore.settings.schema import (
    CONSEQUENCE_REBUILD,
    CONSEQUENCE_RESTART,
    FLOW_HOMES,
    PREFS_HOMES,
    spec_for,
)

if TYPE_CHECKING:
    from kacore.config import Config
    from kacore.providers.registry import ProviderRegistry
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("KnowledgeRepositoryApi")


class SettingsApiMixin:
    _settings_store: SettingsStore | None
    _provider_registry: ProviderRegistry | None
    _config: Config | None
    # 另依赖 KnowledgeRepositoryApi 提供的 _invalidate_embedding_indexes /
    # _apply_rerank_runtime_config / _notify_auto_reindex / _check_zotero_access_mode_switch。

    def _require_settings(self) -> SettingsStore:
        if self._settings_store is None:
            raise RuntimeError("settings store is not configured")
        return self._settings_store

    def _require_registry(self) -> ProviderRegistry:
        if self._provider_registry is None:
            raise RuntimeError("provider registry is not configured")
        return self._provider_registry

    def _refresh_config_from_settings(self) -> None:
        if self._config is not None and self._settings_store is not None:
            self._config.raw = self._settings_store.to_config_raw()

    # ── 设置 ──────────────────────────────────────────────────────

    def get_settings_schema(self, home: str | None = None) -> dict[str, Any]:
        settings = self._require_settings()
        return {
            "home": home or "",
            "flow_homes": list(FLOW_HOMES),
            "prefs_homes": list(PREFS_HOMES),
            "items": settings.schema(home),
        }

    def _validate_model_ref_target(self, key: str, value: Any) -> None:
        ref = ModelRef.parse(value)
        if ref is None:
            return
        registry = self._require_registry()
        profile = registry.get(ref.provider_id)
        if profile is None:
            raise ValueError(f"连接 {ref.provider_id} 不存在")
        if key == "flow.embedding.source" and profile.kind != KIND_OPENAI_COMPATIBLE:
            raise ValueError("Embedding 只能使用 OpenAI 兼容连接（/embeddings 接口）")

    async def update_setting(self, key: str, value: Any) -> dict[str, Any]:
        """写入一个设置项；返回后果（none / restart / rebuild）。非法值抛 ValueError。"""
        settings = self._require_settings()
        try:
            spec = spec_for(key)
        except KeyError:
            raise ValueError(f"未知设置项：{key}") from None
        if spec.kind == "model_ref":
            self._validate_model_ref_target(key, value)
        if key == "flow.zotero.access_mode":
            await self._check_zotero_access_mode_switch(str(value))
        change = settings.set(key, value)
        self._refresh_config_from_settings()
        consequence = change.effective_consequence
        if consequence == CONSEQUENCE_REBUILD:
            await self._invalidate_embedding_indexes(f"Setting changed: {key}")
        if change.changed and key.startswith("flow.retrieval.rerank."):
            self._apply_rerank_runtime_config()
        if change.changed and key == "flow.vector_store.auto_rebuild" and value:
            self._notify_auto_reindex()
        logger.info("update_setting ok: %s consequence=%s", key, consequence)
        return {
            "status": "success",
            "key": key,
            "value": settings.public_value(key),
            "consequence": consequence,
            "restart_required": consequence in (CONSEQUENCE_RESTART, CONSEQUENCE_REBUILD),
            "rebuild_required": consequence == CONSEQUENCE_REBUILD,
        }

    # ── 连接 ──────────────────────────────────────────────────────

    def list_providers(self) -> dict[str, Any]:
        registry = self._require_registry()
        return {
            "providers": registry.public_list(),
            "presets": preset_dicts(),
            "kinds": list(PROVIDER_KINDS),
        }

    def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        registry = self._require_registry()
        profile = registry.create(
            label=str(payload.get("label") or ""),
            base_url=str(payload.get("base_url") or ""),
            kind=str(payload.get("kind") or KIND_OPENAI_COMPATIBLE),
            api_key=str(payload.get("api_key") or ""),
            timeout_sec=int(payload.get("timeout_sec") or 120),
            provider_id=str(payload.get("id") or ""),
        )
        self._refresh_config_from_settings()
        return registry.public(profile)

    async def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """改地址 / 类型 / 超时 / 密钥。被 Embedding 使用的连接换地址 → 索引需重建。"""
        registry = self._require_registry()
        before = registry.require(provider_id)
        profile = registry.update(
            provider_id,
            label=payload.get("label"),
            base_url=payload.get("base_url"),
            kind=payload.get("kind"),
            timeout_sec=payload.get("timeout_sec"),
        )
        if "api_key" in payload:
            registry.set_api_key(provider_id, str(payload.get("api_key") or ""))
        self._refresh_config_from_settings()
        rebuild = (
            "flow.embedding.source" in registry.references(provider_id)
            and profile.base_url != before.base_url
        )
        if rebuild:
            await self._invalidate_embedding_indexes(f"Embedding provider changed: {provider_id}")
        embedding_key_changed = "api_key" in payload and (
            "flow.embedding.source" in registry.references(provider_id)
        )
        return {
            **registry.public(profile),
            "rebuild_required": rebuild,
            "restart_required": rebuild or embedding_key_changed,
        }

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        self._require_registry().delete(provider_id)
        self._refresh_config_from_settings()
        return {"status": "deleted", "id": provider_id}

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        result = await self._require_registry().test(provider_id)
        self._refresh_config_from_settings()
        return result

    async def refresh_provider_models(self, provider_id: str) -> dict[str, Any]:
        try:
            models = await self._require_registry().refresh_models(provider_id)
        except ProviderError as exc:
            return {"status": "error", "code": exc.code, "message": exc.message, "models": []}
        return {"status": "ok", "models": models}


__all__ = ["SettingsApiMixin"]
