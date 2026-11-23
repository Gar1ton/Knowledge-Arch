"""手动检查更新：偏好设置里点「检查更新」时，查本项目在 GitHub 上最新的正式 Release，
与当前版本比较。

用户 11-23：不要自动检测，只要一个按钮手动检查、辅助更新。所以不在后台定时检查，也不自动下载安装；
有新版本时界面给出下载页（`DOWNLOAD_PAGE`，桌面壳只允许打开这一个地址），由用户自己下载安装。
只读一次公开接口，不带任何用户信息；草稿与预发布版不算（GitHub 的 `releases/latest`
本身就排除它们）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import web

RELEASES_REPO = "Gar1ton/Knowledge-Arch"
LATEST_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
DOWNLOAD_PAGE = f"https://github.com/{RELEASES_REPO}/releases/latest"
TIMEOUT_SECONDS = 10

FetchLatest = Callable[[str], Awaitable[dict[str, Any]]]
_FETCH_KEY = web.AppKey("update_fetch_latest", object)
_VERSION_KEY = web.AppKey("update_app_version", str)


def version_key(text: str) -> tuple[int, int, int, int, tuple[str, ...]]:
    """`v3.0.0-preview.1` / `3.0.1` → 可比较的键。

    同一版本号下预发布版低于正式版（语义化版本的规则）。
    """
    core, _, pre = (text or "").strip().lstrip("vV").partition("-")
    nums = [int(p) if p.isdigit() else 0 for p in core.split(".")[:3]]
    nums += [0] * (3 - len(nums))
    return nums[0], nums[1], nums[2], 0 if pre else 1, tuple(pre.split(".")) if pre else ()


def is_newer(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    return version_key(latest) > version_key(current)


async def fetch_latest_release(user_agent: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(LATEST_API) as response:
            if response.status == 404:          # 还没有任何正式 Release
                return {}
            response.raise_for_status()
            data = await response.json()
    return data if isinstance(data, dict) else {}


async def handle_update_check(request: web.Request) -> web.Response:
    """GET /api/update/check — 当前版本、最新正式版本、是否更新，以及下载页地址。"""
    current = request.app[_VERSION_KEY]
    fetch: FetchLatest = request.app[_FETCH_KEY]  # type: ignore[assignment]
    try:
        release = await fetch(f"Knowledge-Arch/{current or 'dev'}")
    except Exception as exc:  # noqa: BLE001 - 网络或接口问题原样告诉用户
        return web.json_response(
            {"status": "error", "message": f"Could not reach GitHub: {exc}"}, status=502
        )
    latest = str(release.get("tag_name") or "").lstrip("vV")
    return web.json_response({
        "current_version": current,
        "latest_version": latest,
        "newer": is_newer(latest, current),
        "published_at": str(release.get("published_at") or ""),
        "download_page": DOWNLOAD_PAGE,
    })


def register_update_routes(
    app: web.Application, app_version: str, fetch: FetchLatest | None = None
) -> None:
    """fetch 供测试注入；缺省直接请求 GitHub。"""
    app[_VERSION_KEY] = app_version
    app[_FETCH_KEY] = fetch or fetch_latest_release
    app.router.add_get("/api/update/check", handle_update_check)


__all__ = ["DOWNLOAD_PAGE", "is_newer", "register_update_routes", "version_key"]
