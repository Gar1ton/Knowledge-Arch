"""ModelRouter：按角色（chat / research / graph / vision）取模型。

`client_for(role)` 返回一个 `RoleClient`，**每次调用时**才读「模型分配」并解析连接与密钥，
所以在界面里改选模型或替换密钥后立即生效，不需要重启服务。research / graph 未单独分配时
跟随 chat；vision 不跟随（对话模型不一定能看图）。没有可用分配时 `configured` 为 False，
LLMAdapter 据此走「未配置模型」分支。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kacore.domain.llm_generation import STATUS_EMPTY, GenerationResult
from kacore.providers.base import (
    ERROR_NOT_FOUND,
    ROLE_CHAT,
    ROLES,
    ROLES_FOLLOWING_CHAT,
    ModelRef,
    ProviderClient,
    ProviderError,
)

if TYPE_CHECKING:
    from kacore.providers.registry import ProviderRegistry
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("ModelRouter")


class ModelRouter:
    def __init__(self, settings: SettingsStore, registry: ProviderRegistry) -> None:
        self._settings = settings
        self._registry = registry

    def ref_for(self, role: str) -> ModelRef | None:
        if role not in ROLES:
            raise ValueError(f"unknown model role: {role}")
        ref = ModelRef.parse(self._settings.get(f"flow.models.{role}"))
        if ref is None and role in ROLES_FOLLOWING_CHAT:
            ref = ModelRef.parse(self._settings.get(f"flow.models.{ROLE_CHAT}"))
        return ref

    def describe(self, role: str) -> str:
        ref = self.ref_for(role)
        return ref.label if ref else ""

    def client_for(
        self, role: str, *, timeout_sec: int | None = None, max_retries: int = 2
    ) -> RoleClient:
        if role not in ROLES:
            raise ValueError(f"unknown model role: {role}")
        return RoleClient(self, role, timeout_sec=timeout_sec, max_retries=max_retries)

    def resolve(
        self, role: str, *, timeout_sec: int | None, max_retries: int
    ) -> ProviderClient | None:
        ref = self.ref_for(role)
        if ref is None:
            return None
        if self._registry.get(ref.provider_id) is None:
            raise ProviderError(
                ERROR_NOT_FOUND, (
                    f"The connection {ref.provider_id} used by “Model assignment” no longer exists"
                )
            )
        return self._registry.client(
            ref.provider_id, ref.model, timeout_sec=timeout_sec, max_retries=max_retries
        )


class RoleClient:
    """满足 `kacore.adapters.llm.ChatClient`；每次调用现取分配，客户端按参数缓存。"""

    def __init__(
        self, router: ModelRouter, role: str, *, timeout_sec: int | None, max_retries: int
    ) -> None:
        self._router = router
        self._role = role
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._cache_key: tuple[object, ...] | None = None
        self._cached: ProviderClient | None = None

    @property
    def role(self) -> str:
        return self._role

    @property
    def configured(self) -> bool:
        ref = self._router.ref_for(self._role)
        return ref is not None and self._router._registry.get(ref.provider_id) is not None

    def _client(self) -> ProviderClient | None:
        ref = self._router.ref_for(self._role)
        if ref is None:
            return None
        registry = self._router._registry
        profile = registry.get(ref.provider_id)
        key = (ref, profile, registry.api_key(ref.provider_id), self._timeout_sec)
        if key != self._cache_key or self._cached is None:
            self._cached = self._router.resolve(
                self._role, timeout_sec=self._timeout_sec, max_retries=self._max_retries
            )
            self._cache_key = key
        return self._cached

    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult:
        client = self._client()
        if client is None:
            if not allow_mock:
                raise ProviderError(ERROR_NOT_FOUND, f"No model is assigned to “{self._role}” yet")
            return GenerationResult(text="", status=STATUS_EMPTY)
        return await client.generate_result(prompt, system_prompt, allow_mock=allow_mock)

    async def describe_image(
        self,
        prompt: str,
        image: bytes,
        system_prompt: str = "",
        *,
        max_tokens: int = 800,
        media_type: str = "image/png",
    ) -> GenerationResult:
        client = self._client()
        if client is None:
            raise ProviderError(ERROR_NOT_FOUND, f"No model is assigned to “{self._role}” yet")
        return await client.describe_image(
            prompt, image, system_prompt, max_tokens=max_tokens, media_type=media_type
        )


__all__ = ["ModelRouter", "RoleClient"]
