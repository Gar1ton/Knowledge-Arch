"""桌面版新增的 HTTP 路由：可选组件（偏好设置 › 组件）、旧版插件数据导入（欢迎页）
与「删除全部数据」的主密钥清除。

与 `server.py` 同样只做 HTTP↔`kacore/api` 的翻译；拆成独立模块是因为 server.py 已超出
文件大小红线。门面经 `register_desktop_routes(app, api_of)` 注入的取值函数获得
（软重启期间返回 503 的逻辑由 server.py 的 `_api` 负责）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

from kacore.api_legacy_import import LegacyImportConflictError
from kacore.legacy_import.layout import LegacyImportError

if TYPE_CHECKING:
    from kacore.api import KnowledgeRepositoryApi

ApiOf = Callable[[web.Request], "KnowledgeRepositoryApi"]


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"status": "error", "message": message}, status=status)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register_desktop_routes(app: web.Application, api_of: ApiOf) -> None:
    """把组件与导入路由挂到 app 上。"""

    # ── 可选组件 ──────────────────────────────────────────────────

    async def components_list(request: web.Request) -> web.Response:
        """GET /api/components — 安装器可用性与各组件状态。"""
        return web.json_response(api_of(request).list_components())

    async def component_install(request: web.Request) -> web.Response:
        """POST /api/components/{key}/install — 用 uv 把组件装进数据目录（白名单内）。"""
        try:
            result = await api_of(request).install_dependency(request.match_info["key"])
        except ValueError as exc:
            return _error(str(exc), 400)
        return web.json_response(result, status=200 if result.get("status") == "ok" else 500)

    async def component_uninstall(request: web.Request) -> web.Response:
        """DELETE /api/components/{key} — 删除组件目录（重启服务后生效）。"""
        try:
            result = api_of(request).uninstall_component(request.match_info["key"])
        except ValueError as exc:
            return _error(str(exc), 400)
        except RuntimeError as exc:
            return _error(str(exc), 501)
        return web.json_response(result, status=200 if result.get("status") == "ok" else 500)

    # ── 旧版插件数据导入 ──────────────────────────────────────────

    def _import_error(exc: Exception) -> web.Response:
        if isinstance(exc, LegacyImportConflictError):
            return _error(str(exc), 409)
        if isinstance(exc, LegacyImportError):
            return _error(str(exc), 400)
        return _error(str(exc), 501)

    async def legacy_inspect(request: web.Request) -> web.Response:
        """POST /api/legacy-import/inspect {path} — 预览所选目录里的旧版数据。"""
        path = (await _json_body(request)).get("path")
        if not isinstance(path, str) or not path.strip():
            return _error("path required", 400)
        try:
            return web.json_response(await api_of(request).inspect_legacy_data(path))
        except (LegacyImportError, RuntimeError) as exc:
            return _import_error(exc)

    async def legacy_start(request: web.Request) -> web.Response:
        """POST /api/legacy-import {path} — 后台导入，完成后自动重启服务换入。"""
        path = (await _json_body(request)).get("path")
        if not isinstance(path, str) or not path.strip():
            return _error("path required", 400)
        try:
            return web.json_response(await api_of(request).start_legacy_import(path), status=202)
        except (LegacyImportError, RuntimeError) as exc:
            return _import_error(exc)

    async def legacy_status(request: web.Request) -> web.Response:
        """GET /api/legacy-import — 进度、待应用的导入与最近一次结果。"""
        try:
            return web.json_response(await api_of(request).get_legacy_import_status())
        except RuntimeError as exc:
            return _import_error(exc)

    async def legacy_discard(request: web.Request) -> web.Response:
        """DELETE /api/legacy-import — 放弃尚未换入的导入。"""
        try:
            return web.json_response(await api_of(request).discard_legacy_import())
        except (LegacyImportError, RuntimeError) as exc:
            return _import_error(exc)

    # ── 删除全部数据 ──────────────────────────────────────────────

    async def forget_master_key(request: web.Request) -> web.Response:
        """POST /api/secrets/forget-master-key — 删除钥匙串里的主密钥；文件由桌面壳随后删除。"""
        try:
            return web.json_response(api_of(request).forget_master_key())
        except RuntimeError as exc:
            return _error(str(exc), 501)

    app.router.add_get("/api/components", components_list)
    app.router.add_post("/api/components/{key}/install", component_install)
    app.router.add_delete("/api/components/{key}", component_uninstall)
    app.router.add_post("/api/legacy-import/inspect", legacy_inspect)
    app.router.add_post("/api/legacy-import", legacy_start)
    app.router.add_get("/api/legacy-import", legacy_status)
    app.router.add_delete("/api/legacy-import", legacy_discard)
    app.router.add_post("/api/secrets/forget-master-key", forget_master_key)


__all__ = ["register_desktop_routes"]
