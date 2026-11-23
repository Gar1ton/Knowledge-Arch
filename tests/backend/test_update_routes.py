"""手动检查更新：版本比较（预发布低于同号正式版）与 /api/update/check 的返回。不联网。"""
from __future__ import annotations

from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from web.update_routes import DOWNLOAD_PAGE, is_newer, register_update_routes


def test_versions_compare_like_semver() -> None:
    assert is_newer("3.0.1", "3.0.0")
    assert is_newer("3.0.0-preview.1", "2.1.1")
    assert not is_newer("3.0.0-preview.1", "3.0.0")       # 预览版低于同号正式版
    assert is_newer("3.0.0", "3.0.0-preview.1")
    assert is_newer("3.0.0-preview.2", "3.0.0-preview.1")
    assert not is_newer("2.2.0", "3.0.0")
    assert not is_newer("", "3.0.0") and not is_newer("3.0.1", "")


async def _client(fetch: Any, version: str = "3.0.0") -> TestClient:
    app = web.Application()
    register_update_routes(app, version, fetch=fetch)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_check_reports_a_newer_release_and_the_download_page() -> None:
    agents: list[str] = []

    async def fetch(user_agent: str) -> dict[str, Any]:
        agents.append(user_agent)
        return {"tag_name": "v3.0.1", "published_at": "2026-12-01T00:00:00Z"}

    client = await _client(fetch)
    try:
        data = await (await client.get("/api/update/check")).json()
    finally:
        await client.close()
    assert data == {"current_version": "3.0.0", "latest_version": "3.0.1", "newer": True,
                    "published_at": "2026-12-01T00:00:00Z", "download_page": DOWNLOAD_PAGE}
    assert agents == ["Knowledge-Arch/3.0.0"]                # 不带任何用户信息


async def test_no_release_yet_is_not_an_update() -> None:
    async def fetch(_ua: str) -> dict[str, Any]:
        return {}

    client = await _client(fetch)
    try:
        data = await (await client.get("/api/update/check")).json()
    finally:
        await client.close()
    assert data["newer"] is False and data["latest_version"] == ""


async def test_network_failure_is_reported_not_raised() -> None:
    async def fetch(_ua: str) -> dict[str, Any]:
        raise OSError("offline")

    client = await _client(fetch)
    try:
        response = await client.get("/api/update/check")
        assert response.status == 502
        assert "offline" in (await response.json())["message"]
    finally:
        await client.close()
