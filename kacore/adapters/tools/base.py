"""联网工具的接口与共用 HTTP（adapters 层）。

为何存在：每个联网功能都是一个 `Tool`，形状照 MCP（spec 给模型看，call 返回内容块）。核心代码
（distributor、账本、设置、界面）只认这个接口；某个来源的请求形态、限速、额度、密钥与字段转换只写在
它自己的工具文件里。加一个来源 = 加一个工具文件 + 在 `registry.BUILTIN` 登记一行。

契约（`Tool`）：
- `call(arguments)`：只取数据，不判断相关性或真伪；失败抛异常即可，由 `ToolBox.call` 统一转成
  is_error 的结果交给模型。返回的块是给模型读的纯文本，每块一条记录或一段原文。
- `status()`：工具面板显示的运行信息（如额度）；键值都由工具自己定，核心只显示不解释。

HTTP 一律同步 urllib，放进线程执行；请求函数可注入，便于离线测试。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.domain.tool_calls import Block, ToolResult, ToolSpec

if TYPE_CHECKING:
    from kacore.repository.reranker.base import Reranker

USER_AGENT = "KnowledgeArch/3.0 (desktop literature assistant)"
DEFAULT_TIMEOUT = 20.0
# 429 时按 Retry-After 等待的上限（秒）；超过就不等，直接报错给模型（假设值）。
MAX_RETRY_WAIT = 5.0
# 检索类工具每次最多返回几条（输入格式里的上限；各家 API 都支持到这个数）。
MAX_RESULTS = 25
DEFAULT_RESULTS = 8

Opener = Callable[..., Any]


class ToolError(RuntimeError):
    """一次请求失败。status 为 HTTP 状态码（网络层错误时为 None）。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ToolEnv:
    """装配工具时可用的本机资源（由组合根给出）；工具按需取用。"""

    cache_dir: Path
    reranker: Reranker | None = None


class Tool(ABC):
    spec: ToolSpec

    @classmethod
    def from_settings(cls, values: Mapping[str, Any], env: ToolEnv) -> Tool:
        """按工具声明的设置装配（values 的键 = ToolSetting.name）；需要本机资源的工具覆盖它。"""
        return cls(**values)

    @abstractmethod
    async def call(self, arguments: Mapping[str, Any]) -> ToolResult: ...

    def status(self) -> dict[str, str]:
        return {}


# ── HTTP ────────────────────────────────────────────────────


class HttpClient:
    """带 User-Agent、超时、最小间隔的 GET；同一实例一次只发一个请求（有的来源限制并发）。
    遇到 429 按 Retry-After 等待（最多 MAX_RETRY_WAIT 秒）重试一次。last_headers 是最近一次
    响应的头（小写键），工具据此读额度等信息。"""

    def __init__(self, name: str, *, opener: Opener | None = None,
                 timeout: float = DEFAULT_TIMEOUT, min_interval: float = 0.0,
                 headers: dict[str, str] | None = None) -> None:
        self._name = name
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.last_headers: dict[str, str] = {}

    def get_bytes(self, url: str, params: Mapping[str, Any] | None = None) -> bytes:
        full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        with self._lock:
            for attempt in (1, 2):
                self._wait_turn()
                request = urllib.request.Request(full, headers=self._headers)  # noqa: S310
                try:
                    with self._opener(request, timeout=self._timeout) as resp:
                        self.last_headers = _lower(getattr(resp, "headers", {}))
                        data: bytes = resp.read()
                        return data
                except urllib.error.HTTPError as exc:
                    self.last_headers = _lower(exc.headers)
                    wait = _retry_after(self.last_headers)
                    if exc.code == 429 and attempt == 1 and wait is not None:
                        time.sleep(wait)
                        continue
                    raise ToolError(f"{self._name}: HTTP {exc.code}", status=exc.code) from exc
                except Exception as exc:  # noqa: BLE001 - 网络层错误统一归类
                    raise ToolError(f"{self._name}: {exc}") from exc
        raise ToolError(f"{self._name}: HTTP 429", status=429)

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        raw = self.get_bytes(url, params)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError(f"{self._name}: invalid JSON ({exc})") from exc

    def _wait_turn(self) -> None:
        delay = self._last_request + self._min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()


def _lower(headers: Any) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    except (TypeError, ValueError):
        return {}


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """Retry-After 秒数（缺省 1 秒）；超过 MAX_RETRY_WAIT 返回 None（不等）。"""
    try:
        value = float(headers.get("retry-after") or 1)
    except ValueError:
        value = 1.0
    return max(value, 0.0) if value <= MAX_RETRY_WAIT else None


async def fetch_json(http: HttpClient, url: str, params: Mapping[str, Any] | None = None) -> Any:
    return await asyncio.to_thread(http.get_json, url, params)


# ── 参数与输出的共用写法 ────────────────────────────────────


def search_schema(query_hint: str) -> dict[str, Any]:
    """检索类工具共用的输入格式：query + limit。"""
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": query_hint},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS,
                      "description": f"Number of results (default {DEFAULT_RESULTS})."},
        },
        "required": ["query"],
    }


def query_args(arguments: Mapping[str, Any]) -> tuple[str, int]:
    """取 query 与 limit；query 为空抛 ValueError（由 ToolBox 转成给模型的错误说明）。"""
    query = " ".join(str(arguments.get("query") or "").split())
    if not query:
        raise ValueError("query is required")
    try:
        limit = int(arguments.get("limit") or DEFAULT_RESULTS)
    except (TypeError, ValueError):
        limit = DEFAULT_RESULTS
    return query, min(max(limit, 1), MAX_RESULTS)


def record_block(lines: Sequence[tuple[str, object]]) -> Block:
    """一条书目记录 → 一块「Label: value」文本；空值的行省略，列表用「; 」连接。"""
    out = []
    for label, value in lines:
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value if str(v).strip())
        text = " ".join(str(value or "").split())
        if text:
            out.append(f"{label}: {text}")
    return Block("\n".join(out))


def no_results(query: str) -> ToolResult:
    return ToolResult([Block(f"No results for: {query}")])


def year_of(value: object) -> str:
    """从 '2026'、2026、'2026-03-01' 取四位年份；取不到返回空串。"""
    text = str(value or "").strip()
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


__all__ = [
    "DEFAULT_RESULTS",
    "MAX_RESULTS",
    "USER_AGENT",
    "HttpClient",
    "Opener",
    "Tool",
    "ToolEnv",
    "ToolError",
    "fetch_json",
    "no_results",
    "query_args",
    "record_block",
    "search_schema",
    "year_of",
]
