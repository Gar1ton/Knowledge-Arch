"""单元测试：NotionRestClient（真实端点与请求头、token 现取、错误分类、频控、分页、重试）。

transport 注入 fake 记录 (method, path, body, query) 并按脚本返回 (status, headers, payload)，
不发真实网络请求；另有一项用本机 aiohttp 服务验证真实 HTTP 往返的请求头。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp import web

import kacore.adapters.notion_rest as notion_rest
from kacore.adapters.notion_rest import (
    NOTION_API_VERSION,
    NotionMCPError,
    NotionRestClient,
)

# ── 测试基建 ────────────────────────────────────────────────────


@dataclass
class FakeTransport:
    """按脚本返回响应；元素为 payload（视为 200）、(status, headers, payload) 或异常。"""

    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, str, Any, Any]] = field(default_factory=list)

    async def __call__(
        self, method: str, path: str, body: Any, query: Any
    ) -> tuple[int, dict[str, str], Any]:
        self.calls.append((method, path, body, query))
        if not self.responses:
            return 200, {}, {"id": "auto-id"}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple):
            return response
        return 200, {}, response


def _client(transport: FakeTransport, **kwargs: Any) -> tuple[NotionRestClient, list[float]]:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    kwargs.setdefault("rate_limit_rps", 0)
    client = NotionRestClient(lambda: "secret_x", transport=transport, sleep=_sleep, **kwargs)
    client._data_sources["db-1"] = "ds-1"
    return client, slept


# ── 端点与载荷 ──────────────────────────────────────────────────


async def test_operations_map_to_rest_endpoints() -> None:
    transport = FakeTransport(
        responses=[
            {"id": "page-1"},
            {},
            {},
            {"results": [], "has_more": False},
            {},
            {"properties": {}},
            {},
        ]
    )
    client, _ = _client(transport)
    await client.create_database_item("db-1", {"Name": {"title": []}})
    await client.update_page_properties("page-1", {"Name": {"title": []}})
    await client.append_blocks("page-1", [{"type": "paragraph"}])
    await client.list_block_children("page-1")
    await client.delete_block("blk-1")
    await client.retrieve_data_source("db-1")
    await client.update_database("db-1", {"Tags": {"multi_select": {}}})
    assert [(m, p) for m, p, _, _ in transport.calls] == [
        ("POST", "/pages"),
        ("PATCH", "/pages/page-1"),
        ("PATCH", "/blocks/page-1/children"),
        ("GET", "/blocks/page-1/children"),
        ("DELETE", "/blocks/blk-1"),
        ("GET", "/data_sources/ds-1"),
        ("PATCH", "/data_sources/ds-1"),
    ]
    assert transport.calls[0][2]["parent"] == {"type": "data_source_id", "data_source_id": "ds-1"}
    assert "children" not in transport.calls[0][2]
    assert transport.calls[3][3] == {"page_size": 100}


async def test_create_database_payload_shape() -> None:
    transport = FakeTransport(responses=[{"id": "db-9"}])
    client, _ = _client(transport)
    assert await client.create_database("parent-9", "Knowledge", {"Name": {"title": {}}}) == "db-9"
    method, path, body, _ = transport.calls[0]
    assert (method, path) == ("POST", "/databases")
    assert body["parent"] == {"type": "page_id", "page_id": "parent-9"}
    assert body["title"][0]["text"]["content"] == "Knowledge"
    assert body["initial_data_source"]["properties"] == {"Name": {"title": {}}}


async def test_append_blocks_splits_into_batches_of_100() -> None:
    transport = FakeTransport()
    client, _ = _client(transport)
    await client.append_blocks("page", [{"n": i} for i in range(250)])
    sizes = [len(body["children"]) for _, _, body, _ in transport.calls]
    assert sizes == [100, 100, 50]
    assert transport.calls[2][2]["children"][0] == {"n": 200}


async def test_missing_id_in_response_raises() -> None:
    client, _ = _client(FakeTransport(responses=[{"object": "page"}]))
    with pytest.raises(NotionMCPError, match="缺少 id"):
        await client.create_database_item("db-1", {})


# ── 分页 ────────────────────────────────────────────────────────


async def test_query_database_paging_and_filter_in_body() -> None:
    transport = FakeTransport(
        responses=[
            {"results": [{"id": "p1"}], "has_more": True, "next_cursor": "cur-2"},
            {"results": [{"id": "p2"}], "has_more": False, "next_cursor": None},
        ]
    )
    client, _ = _client(transport)
    doc_filter = {"property": "DocID", "rich_text": {"equals": "d1"}}
    results = await client.query_database("db-1", filter=doc_filter)
    assert [r["id"] for r in results] == ["p1", "p2"]
    assert transport.calls[0][:2] == ("POST", "/data_sources/ds-1/query")
    assert transport.calls[0][2]["filter"] == doc_filter
    assert "start_cursor" not in transport.calls[0][2]
    assert transport.calls[1][2]["start_cursor"] == "cur-2"


async def test_block_children_cursor_goes_to_query_string() -> None:
    transport = FakeTransport(
        responses=[
            {"results": [{"id": "b1"}], "has_more": True, "next_cursor": "c2"},
            {"results": [{"id": "b2"}], "has_more": False},
        ]
    )
    client, _ = _client(transport)
    assert [b["id"] for b in await client.list_block_children("page")] == ["b1", "b2"]
    assert transport.calls[1][3] == {"page_size": 100, "start_cursor": "c2"}
    assert transport.calls[1][2] is None


async def test_query_database_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notion_rest, "_MAX_QUERY_PAGES", 3)
    transport = FakeTransport(
        responses=[
            {"results": [{"id": f"p{i}"}], "has_more": True, "next_cursor": "same"}
            for i in range(10)
        ]
    )
    client, _ = _client(transport)
    with pytest.raises(NotionMCPError, match="分页上限"):
        await client.query_database("db-1")
    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "response",
    [
        {"results": [], "has_more": True},
        {"results": ["bad"], "has_more": False},
        {"has_more": False},
    ],
)
async def test_malformed_pagination_cannot_return_partial_success(response: dict[str, Any]) -> None:
    client, _ = _client(FakeTransport(responses=[response]))
    with pytest.raises(NotionMCPError):
        await client.query_database("db-1")


# ── 错误分类与重试 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, code, needle",
    [
        (401, "unauthorized", "token"),
        (403, "restricted_resource", "分享"),
        (404, "object_not_found", "object_not_found"),
        (400, "validation_error", "validation_error"),
    ],
)
async def test_http_errors_are_classified_without_retry(
    status: int, code: str, needle: str
) -> None:
    payload = {"object": "error", "status": status, "code": code, "message": "boom detail"}
    transport = FakeTransport(responses=[(status, {}, payload)])
    client, slept = _client(transport)
    with pytest.raises(NotionMCPError) as error:
        await client.delete_block("b1")
    assert error.value.code == code and error.value.status == status
    assert needle in str(error.value)
    assert len(transport.calls) == 1 and slept == []
    assert error.value.is_not_found == (status == 404)


async def test_429_retries_after_retry_after_then_succeeds() -> None:
    transport = FakeTransport(
        responses=[(429, {"Retry-After": "2"}, {"code": "rate_limited"}), {"id": "ok"}]
    )
    client, slept = _client(transport)
    assert await client.create_database_item("db-1", {}) == "ok"
    assert slept == [2.0]
    assert client.request_count == 2


async def test_429_gives_up_after_bounded_retries() -> None:
    transport = FakeTransport(responses=[(429, {}, {"code": "rate_limited"})] * 5)
    client, slept = _client(transport, max_retries=2)
    with pytest.raises(NotionMCPError) as error:
        await client.delete_block("b1")
    assert error.value.code == "rate_limited" and error.value.status == 429
    assert len(transport.calls) == 3 and len(slept) == 2


async def test_server_and_network_errors_retry_with_backoff() -> None:
    transport = FakeTransport(responses=[(502, {}, "bad gateway"), ConnectionError("reset"), {}])
    client, slept = _client(transport)
    assert await client.delete_block("b1") is True
    assert slept == [1.0, 2.0]


async def test_network_error_after_retries_is_explicit() -> None:
    transport = FakeTransport(responses=[ConnectionError("down")] * 4)
    client, _ = _client(transport, max_retries=1)
    with pytest.raises(NotionMCPError) as error:
        await client.delete_block("b1")
    assert error.value.code == "network_error" and error.value.status is None


async def test_missing_token_raises_without_request() -> None:
    transport = FakeTransport()
    client = NotionRestClient(lambda: "  ", transport=transport, rate_limit_rps=0)
    with pytest.raises(NotionMCPError) as error:
        await client.delete_block("b1")
    assert error.value.code == "not_configured"
    assert transport.calls == []


async def test_token_is_read_per_request() -> None:
    seen: list[str] = []
    tokens = iter(["t1", "t2"])

    def _provider() -> str:
        value = next(tokens)
        seen.append(value)
        return value

    client = NotionRestClient(_provider, transport=FakeTransport(), rate_limit_rps=0)
    await client.delete_block("a")
    await client.delete_block("b")
    assert seen == ["t1", "t2"]


# ── 频控与 data source 缓存 ─────────────────────────────────────


async def test_throttle_enforces_min_interval() -> None:
    client = NotionRestClient(lambda: "t", transport=FakeTransport(), rate_limit_rps=10)
    t0 = time.monotonic()
    for block in ("b1", "b2", "b3"):
        await client.delete_block(block)
    assert time.monotonic() - t0 >= 0.19  # 3 次调用至少 2 个间隔（容忍计时抖动）


async def test_data_source_resolution_is_cached_and_config_changes_invalidate_it() -> None:
    transport = FakeTransport(
        responses=[
            {"data_sources": [{"id": "ds-a"}]},
            {"data_sources": [{"id": "ds-a"}, {"id": "ds-b"}]},
        ]
    )
    client = NotionRestClient(lambda: "t", transport=transport, rate_limit_rps=0)
    assert await client.resolve_data_source("db") == "ds-a"
    assert await client.resolve_data_source("db") == "ds-a"
    assert len(transport.calls) == 1
    client.configure_data_sources({"db": "ds-b"})
    assert await client.resolve_data_source("db") == "ds-b"
    assert transport.calls[1][:2] == ("GET", "/databases/db")


# ── 真实 HTTP 往返（本机 aiohttp 服务）─────────────────────────────


async def test_real_http_sends_bearer_and_notion_version() -> None:
    seen: dict[str, Any] = {}

    async def _handler(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["version"] = request.headers.get("Notion-Version")
        seen["query"] = dict(request.query)
        seen["path"] = request.path
        return web.json_response({"results": [{"id": "x"}], "has_more": False})

    app = web.Application()
    app.router.add_route("*", "/v1/blocks/{block_id}/children", _handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        client = NotionRestClient(
            lambda: "secret_real", base_url=f"http://127.0.0.1:{port}/v1", rate_limit_rps=0
        )
        assert await client.list_block_children("page-1") == [{"id": "x"}]
    finally:
        await runner.cleanup()
    assert seen == {
        "auth": "Bearer secret_real",
        "version": NOTION_API_VERSION,
        "query": {"page_size": "100"},
        "path": "/v1/blocks/page-1/children",
    }
