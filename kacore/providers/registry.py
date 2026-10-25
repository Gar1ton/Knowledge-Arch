"""ProviderRegistry：「模型供应商」节点背后的连接管理。

连接（不含密钥）存在 settings.json 的 providers 列表；密钥只在加密库（`provider.<id>.api_key`）。
界面只拿到打码后的后 4 位。「测试连接」先 GET /models（同时刷新模型列表），端点不支持列模型时
退回一次 max_tokens=1 的最小对话请求。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from kacore.providers.base import (
    ERROR_BAD_RESPONSE,
    ERROR_NO_KEY,
    ERROR_NOT_FOUND,
    KIND_ANTHROPIC,
    KIND_OPENAI_COMPATIBLE,
    PROVIDER_KINDS,
    ModelRef,
    ProviderClient,
    ProviderError,
    ProviderProfile,
    api_key_ref_for,
)
from kacore.settings.store import mask_secret

if TYPE_CHECKING:
    from kacore.settings.store import SettingsStore

ClientFactory = Callable[[ProviderProfile, str, str, int, int], ProviderClient]
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
# 引用连接的设置键：删除连接前必须先解除这些引用。
_REFERENCING_KEYS = ("flow.models.chat", "flow.models.research", "flow.models.graph",
                     "flow.embedding.source")


def default_client_factory(
    profile: ProviderProfile, api_key: str, model: str, timeout: int, max_retries: int
) -> ProviderClient:
    if profile.kind == KIND_ANTHROPIC:
        from kacore.providers.anthropic import AnthropicChat

        return AnthropicChat(
            profile.base_url, model, api_key, timeout_seconds=timeout, max_retries=max_retries,
            provider_id=profile.id,
        )
    from kacore.providers.openai_compat import OpenAICompatChat

    return OpenAICompatChat(
        profile.base_url, model, api_key, timeout_seconds=timeout, max_retries=max_retries,
        provider_id=profile.id,
    )


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.strip().lower()).strip("-")
    return slug[:48] or "provider"


class ProviderRegistry:
    """连接的增删改查、密钥、测试与模型列表；不持有任何业务状态以外的东西。"""

    def __init__(
        self, settings: SettingsStore, client_factory: ClientFactory = default_client_factory
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        # 最近一次测试结果（只在内存里，重启后重新测试）。
        self._status: dict[str, dict[str, Any]] = {}

    # ── 读取 ──────────────────────────────────────────────────────

    def list(self) -> list[ProviderProfile]:
        profiles = []
        for raw in self._settings.providers():
            try:
                profiles.append(ProviderProfile.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return profiles

    def get(self, provider_id: str) -> ProviderProfile | None:
        return next((p for p in self.list() if p.id == provider_id), None)

    def require(self, provider_id: str) -> ProviderProfile:
        profile = self.get(provider_id)
        if profile is None:
            raise KeyError(f"provider not found: {provider_id}")
        return profile

    def api_key(self, provider_id: str) -> str:
        profile = self.get(provider_id)
        if profile is None or not profile.api_key_ref:
            return ""
        return self._settings.secrets.get_secret(profile.api_key_ref)

    def public(self, profile: ProviderProfile) -> dict[str, Any]:
        key = self.api_key(profile.id)
        data = profile.to_dict()
        data.pop("api_key_ref", None)
        return {
            **data,
            "api_key_set": bool(key),
            "api_key_masked": mask_secret(key),
            "status": self._status.get(profile.id),
            "referenced_by": self.references(profile.id),
        }

    def public_list(self) -> list[dict[str, Any]]:
        return [self.public(p) for p in self.list()]

    def references(self, provider_id: str) -> list[str]:
        refs = []
        for key in _REFERENCING_KEYS:
            ref = ModelRef.parse(self._settings.get(key))
            if ref is not None and ref.provider_id == provider_id:
                refs.append(key)
        return refs

    # ── 写入 ──────────────────────────────────────────────────────

    def _save(self, profiles: list[ProviderProfile]) -> None:
        self._settings.set_providers(p.to_dict() for p in profiles)

    @staticmethod
    def _validate(kind: str, base_url: str, timeout_sec: int) -> None:
        if kind not in PROVIDER_KINDS:
            raise ValueError(f"kind must be one of {', '.join(PROVIDER_KINDS)}")
        if not re.match(r"^https?://[^\s/]+", base_url):
            raise ValueError("base_url 需要以 http:// 或 https:// 开头")
        if not 1 <= int(timeout_sec) <= 3600:
            raise ValueError("timeout_sec 超出范围 [1, 3600]")

    def create(
        self,
        *,
        label: str,
        base_url: str,
        kind: str = KIND_OPENAI_COMPATIBLE,
        api_key: str = "",
        timeout_sec: int = 120,
        provider_id: str = "",
    ) -> ProviderProfile:
        base_url = base_url.strip().rstrip("/")
        self._validate(kind, base_url, timeout_sec)
        existing = {p.id for p in self.list()}
        candidate = provider_id.strip().lower() or _slug(label)
        if not _ID_RE.match(candidate):
            raise ValueError("provider id 只能包含小写字母、数字、_ 与 -")
        unique, n = candidate, 2
        while unique in existing:
            unique = f"{candidate}-{n}"
            n += 1
        profile = ProviderProfile(
            id=unique,
            kind=kind,
            label=label.strip() or unique,
            base_url=base_url,
            api_key_ref=api_key_ref_for(unique),
            timeout_sec=int(timeout_sec),
        )
        self._save([*self.list(), profile])
        if api_key.strip():
            self._settings.secrets.set_secret(profile.api_key_ref, api_key)
        return profile

    def update(
        self,
        provider_id: str,
        *,
        label: str | None = None,
        base_url: str | None = None,
        kind: str | None = None,
        timeout_sec: int | None = None,
    ) -> ProviderProfile:
        current = self.require(provider_id)
        updated = replace(
            current,
            label=label.strip() if label is not None else current.label,
            base_url=base_url.strip().rstrip("/") if base_url is not None else current.base_url,
            kind=kind if kind is not None else current.kind,
            timeout_sec=int(timeout_sec) if timeout_sec is not None else current.timeout_sec,
        )
        self._validate(updated.kind, updated.base_url, updated.timeout_sec)
        if updated.base_url != current.base_url or updated.kind != current.kind:
            updated = replace(updated, models=())  # 换了地址，旧模型列表作废
            self._status.pop(provider_id, None)
        self._save([updated if p.id == provider_id else p for p in self.list()])
        return updated

    def set_api_key(self, provider_id: str, api_key: str) -> None:
        profile = self.require(provider_id)
        ref = profile.api_key_ref or api_key_ref_for(provider_id)
        if ref != profile.api_key_ref:
            self._save([replace(p, api_key_ref=ref) if p.id == provider_id else p
                        for p in self.list()])
        if api_key.strip():
            self._settings.secrets.set_secret(ref, api_key)
        else:
            self._settings.secrets.delete_secret(ref)
        self._status.pop(provider_id, None)

    def delete(self, provider_id: str) -> None:
        profile = self.require(provider_id)
        refs = self.references(provider_id)
        if refs:
            raise ValueError(f"连接仍被使用：{', '.join(refs)}；请先在「模型分配」中改选其它模型")
        if profile.api_key_ref:
            self._settings.secrets.delete_secret(profile.api_key_ref)
        self._save([p for p in self.list() if p.id != provider_id])
        self._status.pop(provider_id, None)

    # ── 客户端 / 测试 ──────────────────────────────────────────────

    def client(
        self,
        provider_id: str,
        model: str,
        *,
        timeout_sec: int | None = None,
        max_retries: int = 2,
    ) -> ProviderClient:
        profile = self.require(provider_id)
        key = self.api_key(provider_id)
        if profile.kind == KIND_ANTHROPIC and not key:
            raise ProviderError(ERROR_NO_KEY, "该连接还没有填写 API Key")
        timeout = timeout_sec or profile.timeout_sec
        return self._client_factory(profile, key, model, int(timeout), max_retries)

    async def refresh_models(self, provider_id: str) -> list[str]:
        client = self.client(provider_id, "")
        models = await client.list_models()
        self._save([replace(p, models=tuple(models)) if p.id == provider_id else p
                    for p in self.list()])
        return models

    def _probe_model(self, profile: ProviderProfile) -> str:
        for key in _REFERENCING_KEYS:
            ref = ModelRef.parse(self._settings.get(key))
            if ref is not None and ref.provider_id == profile.id:
                return ref.model
        return profile.models[0] if profile.models else ""

    async def test(self, provider_id: str) -> dict[str, Any]:
        """一次最小请求：成功返回模型数；失败返回分类后的 code 与可读 message（不抛错）。"""
        profile = self.require(provider_id)
        started = time.monotonic()
        try:
            try:
                models = await self.refresh_models(provider_id)
                result: dict[str, Any] = {"status": "ok", "models": len(models)}
            except ProviderError as exc:
                if exc.code not in {ERROR_NOT_FOUND, ERROR_BAD_RESPONSE}:
                    raise
                model = self._probe_model(profile)
                if not model:
                    raise ProviderError(
                        exc.code, "该端点不支持列出模型；请先在「模型分配」里选好模型再测试"
                    ) from None
                await self.client(provider_id, model).ping(model)
                result = {"status": "ok", "models": len(profile.models)}
        except ProviderError as exc:
            result = {"status": "error", "code": exc.code, "message": exc.message}
        result["checked_at"] = time.time()
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        self._status[provider_id] = result
        return result


__all__ = ["ClientFactory", "ProviderRegistry", "default_client_factory"]
