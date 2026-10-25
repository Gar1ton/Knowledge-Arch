"""SettingsStore → 既有 `Config` 原始 dict 的桥。

既有 `XxxConfig` dataclass 与各子系统都只认 `Config(raw)`；这里按 SettingSpec.target 把新键
展开成 {section: {key: value}}，再补上需要跨键组合的几项：
- embedding：source=local → provider=local + model=local_model；source=ModelRef →
  provider=external + 连接的 base_url + 模型名 + 连接密钥。指纹载荷（provider/model/base_url）
  与插件版一致，换存储不会让既有索引被判为不兼容。
- models：各角色的「连接:模型」标签，只用于状态展示。
- notion：token（REST 客户端现取，这里只用于数据流节点的就绪判断）。
- providers：连接数量（数据流「模型供应商」节点的状态）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from kacore.config import EmbeddingConfig
from kacore.providers.base import ROLES, ModelRef, ProviderProfile
from kacore.settings.schema import EMBEDDING_SOURCE_LOCAL, SPECS

if TYPE_CHECKING:
    from kacore.settings.store import SettingsStore


def _profiles(store: SettingsStore) -> dict[str, ProviderProfile]:
    profiles: dict[str, ProviderProfile] = {}
    for raw in store.providers():
        try:
            profile = ProviderProfile.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue
        profiles[profile.id] = profile
    return profiles


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_local_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in _LOCAL_HOSTS


def resolve_model_ref(store: SettingsStore, key: str) -> ModelRef | None:
    return ModelRef.parse(store.get(key))


def settings_to_config_raw(store: SettingsStore) -> dict[str, Any]:
    raw: dict[str, dict[str, Any]] = {}
    for spec in SPECS:
        if not spec.target:
            continue
        value = store.get(spec.key)
        for section, key in spec.target:
            raw.setdefault(section, {})[key] = value

    profiles = _profiles(store)
    embedding = raw.setdefault("embedding", {})
    source = store.get("flow.embedding.source")
    ref = ModelRef.parse(source)
    if source == EMBEDDING_SOURCE_LOCAL:
        embedding.update(
            provider="local",
            model=store.get("flow.embedding.local_model"),
            base_url=EmbeddingConfig.base_url,
        )
    else:
        # 默认线上：还没选连接（或连接已删除）时 base_url 为空，节点显示待配置。
        profile = profiles.get(ref.provider_id) if ref else None
        embedding.update(
            provider="external",
            model=ref.model if ref else "",
            base_url=profile.base_url if profile else "",
            api_key=(
                store.secrets.get_secret(profile.api_key_ref)
                if profile and profile.api_key_ref
                else ""
            ),
        )

    # 线上 Rerank：地址与密钥来自所选连接；没选连接则不重排。
    rerank = raw.setdefault("rerank", {})
    if store.get("flow.retrieval.rerank.provider") == "remote":
        rerank_ref = resolve_model_ref(store, "flow.retrieval.rerank.remote")
        rerank_profile = profiles.get(rerank_ref.provider_id) if rerank_ref else None
        if rerank_ref is None or rerank_profile is None:
            rerank["provider"] = "noop"
        else:
            rerank.update(
                model=rerank_ref.model,
                base_url=rerank_profile.base_url,
                api_key=(
                    store.secrets.get_secret(rerank_profile.api_key_ref)
                    if rerank_profile.api_key_ref
                    else ""
                ),
            )

    models: dict[str, Any] = {}
    for role in ROLES:
        role_ref = resolve_model_ref(store, f"flow.models.{role}")
        models[role] = role_ref.label if role_ref else ""
    graph_ref = resolve_model_ref(store, "flow.models.graph") or resolve_model_ref(
        store, "flow.models.chat"
    )
    graph_profile = profiles.get(graph_ref.provider_id) if graph_ref else None
    models["graph_is_local"] = bool(graph_profile and _is_local_url(graph_profile.base_url))
    raw["models"] = models
    raw["providers"] = {"count": len(profiles)}
    raw.setdefault("notion_sync", {})["token"] = store.get("flow.notion.token")
    return raw


__all__ = ["resolve_model_ref", "settings_to_config_raw"]
