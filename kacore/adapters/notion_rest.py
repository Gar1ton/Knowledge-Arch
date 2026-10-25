"""Notion REST API 客户端（adapters 层，防腐层）。

职责：把同步层的结构化请求翻译成 Notion 公开 REST API 调用（`https://api.notion.com/v1`，
请求头 `Notion-Version: 2026-03-11`），并统一处理鉴权、全局频控（默认 3 req/s）、分页聚合、
错误分类（401 / 403 / 404 / 429 / 5xx）与显式报错。

为什么存在：桌面版不再依赖宿主托管的 MCP 客户端，也不要求用户机器装 Node.js；token 在
数据流「Notion」节点填写（设置键 `flow.notion.token`），每次请求现取，换 token 无需重启。

公开方法与错误语义沿用旧的 MCP 适配层（retrieve_database / retrieve_data_source /
query_database / create_database_item / append_blocks / … 与 `NotionMCPError`），因此
`repository/sync_targets/notion.py` 的同步语义（preserve / strict、QA outbox）不受影响。
`NotionMCPError` 这个名字为兼容同步层而保留，含义是「Notion 调用失败」，与传输方式无关。

database 是容器，query / schema / item 操作解析到明确的 data_source_id；多源库必须指定目标。
测试经 `transport` 注入 fake，不发真实网络请求。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("NotionRestClient")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"

# 单次 query/list 的分页上限（100 条/页 → 2 万条足够任何正常库）。
_MAX_QUERY_PAGES = 200
# Notion 单次 append 最多 100 个子块；超出由本层分批，调用方无感。
_MAX_APPEND_CHILDREN = 100
# 429 / 5xx / 网络错误的有界重试；Retry-After 缺失时按指数退避。
_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0
_MAX_RETRY_AFTER_SECONDS = 60.0

# 错误码（NotionMCPError.code）：Notion 自身的 code 原样透传，以下为本层补充的分类。
CODE_NOT_CONFIGURED = "not_configured"
CODE_UNAUTHORIZED = "unauthorized"
CODE_RESTRICTED = "restricted_resource"
CODE_NOT_FOUND = "object_not_found"
CODE_RATE_LIMITED = "rate_limited"
CODE_SERVER_ERROR = "server_error"
CODE_NETWORK = "network_error"
CODE_INCOMPATIBLE = "incompatible_response"

# 测试注入口：async (method, path, body, query) -> (status, headers, payload)
Transport = Callable[
    [str, str, "dict[str, Any] | None", "dict[str, Any] | None"],
    Awaitable[tuple[int, dict[str, str], Any]],
]
TokenProvider = Callable[[], str]


class NotionMCPError(RuntimeError):
    """Notion 调用失败：未配置 token、HTTP 错误、网络错误或响应形状不可用。

    message 面向用户可读（含操作与建议动作）；code 为 Notion 错误码或本模块的 CODE_*，
    status 为 HTTP 状态码（网络错误时为 None）。调用方捕获后应记 failed / degraded 账并
    向上反馈，禁止吞掉当成功。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def is_not_found(self) -> bool:
        """Notion 明确报告远端对象不存在；删除调用可把它视为幂等 miss。"""
        text = str(self).lower()
        return (
            self.code == CODE_NOT_FOUND
            or self.status == 404
            or "object_not_found" in text
            or "404" in text
        )


def classify_http_error(method: str, path: str, status: int, payload: Any) -> NotionMCPError:
    """把 Notion 的错误响应翻译成带中文提示的 NotionMCPError。"""
    code = ""
    detail = ""
    if isinstance(payload, dict):
        code = str(payload.get("code") or "")
        detail = str(payload.get("message") or "")
    elif payload:
        detail = str(payload)
    detail = detail.strip()[:500]
    where = f"{method} {path}"
    if status == 401:
        hint = (
            "The Notion token is invalid or was revoked: enter the integration token again in the "
            "“Notion” node of the data flow."
        )
        return NotionMCPError(f"{hint} ({where})", code=code or CODE_UNAUTHORIZED, status=status)
    if status == 403:
        hint = (
            "This integration cannot access the page or database: share the target page with the "
            "integration in Notion."
        )
        return NotionMCPError(
            f"{hint} ({where}: {detail or code})", code=code or CODE_RESTRICTED, status=status
        )
    if status == 404:
        hint = (
            "The Notion object does not exist or is not shared with this integration "
            "(object_not_found; a 404 cannot tell the two apart)."
        )
        return NotionMCPError(f"{hint} ({where})", code=code or CODE_NOT_FOUND, status=status)
    if status == 429:
        return NotionMCPError(
            f"Notion is rate limiting (HTTP 429) even after retries; try again later ({where}).",
            code=code or CODE_RATE_LIMITED,
            status=status,
        )
    if status >= 500:
        return NotionMCPError(
            f"Notion server error (HTTP {status}); try again later ({where}).",
            code=code or CODE_SERVER_ERROR,
            status=status,
        )
    label = f"{code}: {detail}" if code and detail else (detail or code or "(no error details)")
    return NotionMCPError(
        f"Notion rejected the request (HTTP {status}, {where}): {label}", code=code, status=status
    )


def _retry_after_seconds(headers: dict[str, str], attempt: int) -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after") or ""
    try:
        value = float(raw)
    except ValueError:
        value = _DEFAULT_BACKOFF_SECONDS * (2**attempt)
    return max(0.0, min(value, _MAX_RETRY_AFTER_SECONDS))


class NotionRestClient:
    """Notion REST 客户端：真实端点 + 显式错误 + 全局频控。

    契约：
    - 所有公开方法失败一律抛 NotionMCPError（含未配置 token / 网络错误 / 非 2xx / 形状异常）。
    - 频控在 `_request` 统一执行（min-interval = 1/rate_limit_rps），业务层不得再自行 sleep。
    - 429 按 Retry-After 有界重试；5xx 与网络错误按指数退避有界重试；其余 4xx 不重试。
    - token 每次请求经 token_provider 现取；为空时抛 code=not_configured，不发请求。
    """

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        transport: Transport | None = None,
        base_url: str = NOTION_API_BASE,
        read_timeout_sec: int = 30,
        rate_limit_rps: float = 3.0,
        max_retries: int = _MAX_RETRIES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._read_timeout_sec = read_timeout_sec
        self._min_interval = 1.0 / rate_limit_rps if rate_limit_rps > 0 else 0.0
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep
        self._last_call_monotonic = 0.0
        self._throttle_lock = asyncio.Lock()
        self.request_count = 0
        self._data_sources: dict[str, str] = {}
        self._configured_sources: dict[str, str] = {}

    # ── 传输 ────────────────────────────────────────────────────

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._throttle_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call_monotonic)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_monotonic = time.monotonic()

    def _token(self) -> str:
        try:
            token = str(self._token_provider() or "").strip()
        except Exception as exc:  # noqa: BLE001 - 机密库读取失败等同未配置
            logger.warning("Failed to read the Notion token: %s", type(exc).__name__)
            token = ""
        if not token:
            raise NotionMCPError(
                "Notion is not connected: enter the integration token in the “Notion” node of the "
                "data flow first.",
                code=CODE_NOT_CONFIGURED,
            )
        return token

    async def _http(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        query: dict[str, Any] | None,
        token: str,
    ) -> tuple[int, dict[str, str], Any]:
        """真实 HTTP 往返（aiohttp）；返回 (status, headers, 解析后的 JSON 或文本)。"""
        import aiohttp

        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._read_timeout_sec)
        params = {k: str(v) for k, v in (query or {}).items() if v is not None}
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.request(
                method,
                f"{self._base_url}{path}",
                json=body,
                params=params or None,
                headers=headers,
            ) as resp:
                text = await resp.text()
                try:
                    payload: Any = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    payload = text
                return resp.status, dict(resp.headers), payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        """一次带频控与有界重试的请求；成功返回 JSON，失败抛 NotionMCPError。"""
        token = self._token()
        attempt = 0
        while True:
            await self._throttle()
            self.request_count += 1
            try:
                if self._transport is not None:
                    status, headers, payload = await self._transport(method, path, body, query)
                else:
                    status, headers, payload = await self._http(method, path, body, query, token)
            except NotionMCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络层异常统一分类
                if attempt < self._max_retries:
                    await self._sleep(_DEFAULT_BACKOFF_SECONDS * (2**attempt))
                    attempt += 1
                    continue
                kind = (
                    "request timed out"
                ) if isinstance(exc, asyncio.TimeoutError) else "network error"
                raise NotionMCPError(
                    f"Cannot reach Notion ({kind}: {type(exc).__name__}, {method} {path}). Check "
                    "the network or proxy settings.",
                    code=CODE_NETWORK,
                ) from exc
            if 200 <= status < 300:
                return payload
            if (status == 429 or status >= 500) and attempt < self._max_retries:
                delay = (
                    _retry_after_seconds(headers, attempt)
                    if status == 429
                    else _DEFAULT_BACKOFF_SECONDS * (2**attempt)
                )
                logger.info((
                    "Notion HTTP %s, retrying in %.1fs (%s %s)"
                ), status, delay, method, path)
                await self._sleep(delay)
                attempt += 1
                continue
            raise classify_http_error(method, path, status, payload)

    async def check_capabilities(self, *, initialize: bool = False) -> None:
        """只读检查：token 已填写且有效（GET /users/me）；失败不缓存任何解析结果。

        initialize 仅为兼容同步层签名：REST API 的建库能力随 token 权限而定，无需单独探测。
        """
        del initialize
        self._data_sources.clear()  # 每轮重新验证目标，避免沿用旧 token / 旧容器结果。
        data = await self._request("GET", "/users/me")
        if not isinstance(data, dict) or not data.get("id"):
            raise NotionMCPError((
                "Unexpected shape of the Notion /users/me response."
            ), code=CODE_INCOMPATIBLE)

    @staticmethod
    def _require_id(data: Any, operation: str) -> str:
        if isinstance(data, dict):
            value = data.get("id")
            if value:
                return str(value)
        raise NotionMCPError(f"Notion {operation} returned a result without an id field.")

    async def _paged_results(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按 Notion 分页协议（has_more/next_cursor）聚合 results，带页数上限。

        POST 类分页把 start_cursor 放进 body，GET 类放进 query string。
        """
        all_results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        for _ in range(_MAX_QUERY_PAGES):
            page_body = dict(body) if body is not None else None
            page_query = dict(query) if query is not None else None
            if start_cursor:
                if page_body is not None:
                    page_body["start_cursor"] = start_cursor
                else:
                    page_query = {**(page_query or {}), "start_cursor": start_cursor}
            data = await self._request(method, path, body=page_body, query=page_query)
            if not isinstance(data, dict):
                raise NotionMCPError(f"Unexpected shape of the Notion {method} {path} result.")
            results = data.get("results")
            if not isinstance(results, list):
                raise NotionMCPError(f"Notion {method} {path} returned no results.")
            if any(not isinstance(r, dict) for r in results):
                raise NotionMCPError(f"Notion {method} {path} returned an invalid page entry.")
            all_results.extend(results)
            start_cursor = data.get("next_cursor")
            if data.get("has_more") and not start_cursor:
                raise NotionMCPError(f"Notion {method} {path} pagination has no next_cursor.")
            if not (data.get("has_more") and start_cursor):
                return all_results
        raise NotionMCPError(
            f"Notion {method} {path} exceeded the page limit; partial results are not treated as "
            "complete."
        )

    # ── data source 解析 ────────────────────────────────────────

    def configure_data_sources(self, sources: dict[str, str]) -> None:
        """登记明确的容器→源配置；改变目标时清除解析缓存。"""
        for database_id, source_id in sources.items():
            if self._configured_sources.get(database_id, "") != source_id:
                self._data_sources.pop(database_id, None)
            self._configured_sources[database_id] = source_id

    async def resolve_data_source(self, database_id: str) -> str:
        """验证配置源属于容器；单源自动解析，零源/多源歧义显式失败。"""
        if database_id in self._data_sources:
            return self._data_sources[database_id]
        data = await self.retrieve_database(database_id)
        if "data_sources" not in data:
            raise NotionMCPError(
                "The Notion database response has no data_sources: the target may not be a "
                "database, or the API version is incompatible.",
                code=CODE_INCOMPATIBLE,
            )
        sources = data.get("data_sources")
        if not isinstance(sources, list) or any(
            not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"].strip()
            for s in sources
        ):
            raise NotionMCPError((
                "Unexpected shape of the Notion data_sources field."
            ), code="invalid_response")
        ids = [s["id"] for s in sources]
        if not ids:
            raise NotionMCPError((
                "The target database has no data source; check the target database."
            ), code="no_data_source")
        configured = self._configured_sources.get(database_id, "")
        if configured:
            if configured not in ids:
                raise NotionMCPError(
                    "The configured data_source_id does not belong to the target database; check "
                    "the settings.",
                    code="data_source_mismatch",
                )
            source_id = configured
        elif len(ids) == 1:
            source_id = ids[0]
        else:
            raise NotionMCPError(
                "The target database has several data sources; set data_source_id explicitly.",
                code="ambiguous_data_source",
            )
        self._data_sources[database_id] = source_id
        return source_id

    async def retrieve_data_source(self, database_id: str) -> dict[str, Any]:
        """按容器解析目标 data source，读取完整 schema。"""
        source_id = await self.resolve_data_source(database_id)
        data = await self._request("GET", f"/data_sources/{source_id}")
        if not isinstance(data, dict) or not isinstance(data.get("properties"), dict):
            raise NotionMCPError("The Notion data source result has no properties.")
        return data

    # ── Database 操作 ───────────────────────────────────────────

    async def create_database(
        self, parent_page_id: str, title: str, properties: dict[str, Any]
    ) -> str:
        """在指定 parent page 下创建 database（含初始 data source），返回 database_id。"""
        data = await self._request(
            "POST",
            "/databases",
            body={
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "title": [{"type": "text", "text": {"content": title}}],
                "initial_data_source": {"properties": properties},
            },
        )
        return self._require_id(data, "create database")

    async def retrieve_database(self, database_id: str) -> dict[str, Any]:
        """读取 database 容器（含 data_sources，不含属性 schema）。"""
        data = await self._request("GET", f"/databases/{database_id}")
        if not isinstance(data, dict):
            raise NotionMCPError("Unexpected shape of the Notion retrieve-database result.")
        return data

    async def update_database(self, database_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """便利封装：解析容器后向 data source 增补/修改属性列。"""
        source_id = await self.resolve_data_source(database_id)
        data = await self._request(
            "PATCH", f"/data_sources/{source_id}", body={"properties": properties}
        )
        return data if isinstance(data, dict) else {}

    async def query_database(
        self,
        database_id: str,
        *,
        filter: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 database 页面列表（含完整属性），自动分页聚合；filter 透传 Notion 过滤器。"""
        source_id = await self.resolve_data_source(database_id)
        body: dict[str, Any] = {"page_size": page_size}
        if filter is not None:
            body["filter"] = filter
        return await self._paged_results("POST", f"/data_sources/{source_id}/query", body=body)

    async def create_database_item(self, database_id: str, properties: dict[str, Any]) -> str:
        """在 database 中新建一行（页面），返回 page_id。

        正文块不随建页提交（与旧适配层契约一致），须随后经 append_blocks(page_id, ...) 追加。
        """
        source_id = await self.resolve_data_source(database_id)
        data = await self._request(
            "POST",
            "/pages",
            body={
                "parent": {"type": "data_source_id", "data_source_id": source_id},
                "properties": properties,
            },
        )
        return self._require_id(data, "create page")

    # ── Page / Block 操作 ───────────────────────────────────────

    async def update_page_properties(
        self, page_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """更新页面（database 行）的属性。"""
        data = await self._request("PATCH", f"/pages/{page_id}", body={"properties": properties})
        return data if isinstance(data, dict) else {}

    async def append_blocks(
        self, block_id: str, children: list[dict[str, Any]]
    ) -> None:
        """向页面/块追加子块（页面正文写入的唯一通道）；超过 100 个时按序分批。"""
        for start in range(0, max(len(children), 1), _MAX_APPEND_CHILDREN):
            batch = children[start : start + _MAX_APPEND_CHILDREN]
            await self._request(
                "PATCH", f"/blocks/{block_id}/children", body={"children": batch}
            )

    async def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        """列出块的子块（正文重写前盘点用），自动分页聚合。"""
        return await self._paged_results(
            "GET", f"/blocks/{block_id}/children", query={"page_size": 100}
        )

    async def delete_block(self, block_id: str) -> bool:
        """删除（移入回收站）一个块；页面也是块，归档页面同样走此接口。"""
        await self._request("DELETE", f"/blocks/{block_id}")
        return True


__all__ = [
    "NOTION_API_BASE",
    "NOTION_API_VERSION",
    "NotionMCPError",
    "NotionRestClient",
    "Transport",
    "classify_http_error",
]
