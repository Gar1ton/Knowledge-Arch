"""模型供应商接口（接口先行，见 ../../ARCHITECTURE.md §4）。

一个「连接」= 地址 + 密钥引用（`ProviderProfile`）；一个「模型」= 连接 + 模型名（`ModelRef`）。
密钥本身不进这些对象：`api_key_ref` 指向加密库里的条目，只有发请求前才现取。

`ProviderClient` 是一个连接的调用契约：对话生成、列模型、最小探活。生产实现见
`openai_compat.py` / `anthropic.py`；测试用桩满足同一接口即可。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kacore.domain.llm_generation import GenerationResult

KIND_OPENAI_COMPATIBLE = "openai_compatible"
KIND_ANTHROPIC = "anthropic"
PROVIDER_KINDS = (KIND_OPENAI_COMPATIBLE, KIND_ANTHROPIC)

# 角色：一处列出全部 LLM 开销（数据流「模型分配」节点）。
ROLE_CHAT = "chat"
ROLE_RESEARCH = "research"
ROLE_GRAPH = "graph"
# 识图（2.1.0）：给图表空位写描述。必须选一个能收图片的模型，**不跟随对话模型**。
ROLE_VISION = "vision"
ROLES = (ROLE_CHAT, ROLE_RESEARCH, ROLE_GRAPH, ROLE_VISION)
# 未单独分配时跟随对话模型的角色。
ROLES_FOLLOWING_CHAT = (ROLE_RESEARCH, ROLE_GRAPH)

# 错误分类：界面按 code 给出可读提示，而不是把原始异常文本丢给用户。
ERROR_AUTH = "auth_invalid"  # 401/403：API Key 无效或无权限
ERROR_NOT_FOUND = "not_found"  # 404：地址或模型不存在
ERROR_RATE_LIMITED = "rate_limited"  # 429
ERROR_UNREACHABLE = "unreachable"  # 连接被拒绝 / DNS / TLS
ERROR_TIMEOUT = "timeout"
ERROR_SERVER = "server_error"  # 5xx
ERROR_BAD_RESPONSE = "bad_response"  # 2xx 但响应形状不可用
ERROR_NO_KEY = "missing_api_key"  # 连接需要密钥但尚未填写
ERROR_NO_IMAGE_INPUT = "no_image_input"  # 模型或端点不收图片


def api_key_ref_for(provider_id: str) -> str:
    """连接密钥在加密库里的条目名（只含小写字母数字与 . _ -）。"""
    return f"provider.{provider_id}.api_key"


@dataclass(frozen=True)
class ProviderProfile:
    """一个模型供应商连接。v1 只有两种 kind：OpenAI 兼容覆盖大多数国内外服务。"""

    id: str
    kind: str
    label: str
    base_url: str
    api_key_ref: str = ""
    timeout_sec: int = 120
    models: tuple[str, ...] = ()  # 「获取模型列表」的缓存

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "base_url": self.base_url,
            "api_key_ref": self.api_key_ref,
            "timeout_sec": self.timeout_sec,
            "models": list(self.models),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProviderProfile:
        return cls(
            id=str(raw["id"]),
            kind=str(raw.get("kind") or KIND_OPENAI_COMPATIBLE),
            label=str(raw.get("label") or raw["id"]),
            base_url=str(raw.get("base_url") or "").rstrip("/"),
            api_key_ref=str(raw.get("api_key_ref") or ""),
            timeout_sec=int(raw.get("timeout_sec") or 120),
            models=tuple(str(m) for m in raw.get("models") or ()),
        )


@dataclass(frozen=True)
class ModelRef:
    """连接 + 模型名。"""

    provider_id: str
    model: str

    def to_dict(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "model": self.model}

    @classmethod
    def parse(cls, raw: object) -> ModelRef | None:
        """接受 {"provider_id", "model"} 或 None；其它形状返回 None。"""
        if isinstance(raw, ModelRef):
            return raw
        if not isinstance(raw, dict):
            return None
        provider_id = str(raw.get("provider_id") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not provider_id or not model:
            return None
        return cls(provider_id, model)

    @property
    def label(self) -> str:
        return f"{self.provider_id}:{self.model}"


@dataclass
class ProviderError(RuntimeError):
    """一次供应商调用的失败；message 面向用户，不含密钥或请求正文。"""

    code: str
    message: str
    status: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class ProviderClient(ABC):
    """一个连接的调用契约。

    - `generate_result`：与 `kacore.adapters.llm.ChatClient` 同签名。allow_mock=False 时失败抛
      `ProviderError`；allow_mock=True 时失败返回 status!=ok 的结果。每次真实尝试都记一次用量账。
    - `list_models`：返回模型名列表；端点不支持时抛 `ProviderError(code=not_found)`。
    - `ping`：用最小请求（max_tokens=1）验证地址、密钥与模型可用。
    """

    @abstractmethod
    async def generate_result(
        self, prompt: str, system_prompt: str = "", *, allow_mock: bool = True
    ) -> GenerationResult: ...

    async def describe_image(
        self, prompt: str, image_png: bytes, system_prompt: str = "", *, max_tokens: int = 800
    ) -> GenerationResult:
        """看一张 PNG 图并按 prompt 作答（识图角色）。失败抛 `ProviderError`；
        端点不收图片时为 `ERROR_NO_IMAGE_INPUT`。默认实现：不支持图片。"""
        raise ProviderError(ERROR_NO_IMAGE_INPUT, "This connection does not accept image input")

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    @abstractmethod
    async def ping(self, model: str) -> None: ...


__all__ = [
    "ERROR_AUTH",
    "ERROR_BAD_RESPONSE",
    "ERROR_NOT_FOUND",
    "ERROR_NO_KEY",
    "ERROR_RATE_LIMITED",
    "ERROR_SERVER",
    "ERROR_TIMEOUT",
    "ERROR_UNREACHABLE",
    "KIND_ANTHROPIC",
    "KIND_OPENAI_COMPATIBLE",
    "PROVIDER_KINDS",
    "ROLES",
    "ROLES_FOLLOWING_CHAT",
    "ROLE_VISION",
    "ERROR_NO_IMAGE_INPUT",
    "ROLE_CHAT",
    "ROLE_GRAPH",
    "ROLE_RESEARCH",
    "ModelRef",
    "ProviderClient",
    "ProviderError",
    "ProviderProfile",
    "api_key_ref_for",
]
