"""Notion REST 客户端的测试注入口：把 REST 请求翻译成「操作名 + 参数」交给 fake。

同步层测试的 fake 按操作名路由响应（如 `notion_query_data_source`），这里把
`NotionRestClient` 发出的 (method, path, body, query) 映射回这些操作名，使既有用例的
场景脚本不随传输方式变化。`GET /users/me`（token 校验）默认直接成功、不经 fake。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from kacore.adapters.notion_rest import NotionRestClient

OperationCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]

_ROUTES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    ("GET", re.compile(r"^/databases/([^/]+)$"), "notion_retrieve_database", "database_id"),
    ("POST", re.compile(r"^/databases$"), "notion_create_database", ""),
    ("GET", re.compile(r"^/data_sources/([^/]+)$"), "notion_retrieve_data_source",
     "data_source_id"),
    ("PATCH", re.compile(r"^/data_sources/([^/]+)$"), "notion_update_data_source",
     "data_source_id"),
    ("POST", re.compile(r"^/data_sources/([^/]+)/query$"), "notion_query_data_source",
     "data_source_id"),
    ("POST", re.compile(r"^/pages$"), "notion_create_data_source_item", ""),
    ("PATCH", re.compile(r"^/pages/([^/]+)$"), "notion_update_page_properties", "page_id"),
    ("GET", re.compile(r"^/blocks/([^/]+)/children$"), "notion_retrieve_block_children",
     "block_id"),
    ("PATCH", re.compile(r"^/blocks/([^/]+)/children$"), "notion_append_block_children",
     "block_id"),
    ("DELETE", re.compile(r"^/blocks/([^/]+)$"), "notion_delete_block", "block_id"),
)


def operation_for(
    method: str, path: str, body: dict[str, Any] | None, query: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    """REST 请求 → (操作名, 参数)。参数形状与同步层用例断言的字段一致。"""
    for route_method, pattern, name, id_field in _ROUTES:
        match = pattern.match(path)
        if route_method != method or match is None:
            continue
        args: dict[str, Any] = {**(query or {}), **(body or {})}
        if id_field:
            args[id_field] = match.group(1)
        if name == "notion_create_data_source_item":
            parent = args.pop("parent", {})
            args["data_source_id"] = parent.get("data_source_id", "")
        if name in {"notion_query_data_source", "notion_retrieve_block_children"}:
            args["response_mode"] = "full"
        return name, args
    raise AssertionError(f"unexpected Notion request: {method} {path}")


def rest_transport(
    caller: OperationCaller,
    *,
    users_me: Any = None,
) -> Callable[..., Awaitable[tuple[int, dict[str, str], Any]]]:
    """把操作名 fake 包装成 NotionRestClient 的 transport；fake 抛出的异常原样上抛。"""

    async def _transport(
        method: str, path: str, body: dict[str, Any] | None, query: dict[str, Any] | None
    ) -> tuple[int, dict[str, str], Any]:
        if method == "GET" and path == "/users/me":
            if isinstance(users_me, Exception):
                raise users_me
            return 200, {}, users_me if users_me is not None else {"object": "user", "id": "bot"}
        name, args = operation_for(method, path, body, query)
        result = await caller(name, args)
        return 200, {}, result if result is not None else {}

    return _transport


def notion_client(
    caller: OperationCaller,
    *,
    rate_limit_rps: float = 1000,
    token: str = "secret_test",
    users_me: Any = None,
) -> NotionRestClient:
    """同步层测试用客户端：token 固定、fake 驱动、默认不受频控拖慢。"""
    return NotionRestClient(
        lambda: token,
        transport=rest_transport(caller, users_me=users_me),
        rate_limit_rps=rate_limit_rps,
    )
