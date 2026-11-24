"""3.0.0 新增的 HTTP 路由：外部论文入库（来源栏入库框、待补 PDF 清单、文档出处）与 Zotero 推送。

与 `server.py` 同样只做 HTTP↔`kacore/api` 的翻译；拆成独立模块是因为 server.py 已超出文件大小红线。
补 PDF 的上传沿用 `POST /api/documents`（multipart 多带 import_id），见 server.py。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from kacore.api import KnowledgeRepositoryApi

ApiOf = Callable[[web.Request], "KnowledgeRepositoryApi"]


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"status": "error", "message": message}, status=status)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _ord(request: web.Request) -> int | None:
    try:
        return int(request.match_info["ord"])
    except (KeyError, ValueError):
        return None


def register_library_io_routes(app: web.Application, api_of: ApiOf, app_version: str = "") -> None:
    """把入库、推送、导出导入的路由挂到 app 上。"""

    async def import_preview(request: web.Request) -> web.Response:
        """GET /api/external/captures/{capture_id}/sources/{ord}/import — 入库框的内容。"""
        ord_ = _ord(request)
        if ord_ is None:
            return _error("invalid source number", 400)
        try:
            result = await api_of(request).import_preview(request.match_info["capture_id"], ord_)
        except ValueError as exc:
            return _error(str(exc), 404)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        return web.json_response(result)

    async def import_source(request: web.Request) -> web.Response:
        """POST /api/external/captures/{capture_id}/sources/{ord}/import
        {collection_key, filename} — 入库一篇；找不到 PDF 时返回待补 PDF 的记录。"""
        ord_ = _ord(request)
        body = await _json_body(request)
        key = str(body.get("collection_key") or "").strip()
        if ord_ is None or not key:
            return _error("source number and collection_key are required", 400)
        try:
            result = await api_of(request).import_external_source(
                request.match_info["capture_id"], ord_, collection_key=key,
                filename=str(body.get("filename") or ""))
        except ValueError as exc:
            return _error(str(exc), 400)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        return web.json_response(result)

    async def imports_list(request: web.Request) -> web.Response:
        """GET /api/imports?status=pending_pdf — 入库记录（文档栏的待补 PDF 清单）。"""
        try:
            rows = await api_of(request).list_paper_imports(request.query.get("status") or None)
        except ValueError as exc:
            return _error(str(exc), 400)
        return web.json_response(rows)

    async def import_dismiss(request: web.Request) -> web.Response:
        """POST /api/imports/{import_id}/dismiss — 从待补清单里删掉。"""
        try:
            result = await api_of(request).dismiss_paper_import(request.match_info["import_id"])
        except ValueError as exc:
            return _error(str(exc), 404)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        return web.json_response(result)

    async def import_origin(request: web.Request) -> web.Response:
        """GET /api/documents/{doc_id}/import-origin — 文档信息栏的出处；不是入库来的返回 null。"""
        return web.json_response(
            await api_of(request).document_import_origin(request.match_info["doc_id"]))

    async def push_preview(request: web.Request) -> web.Response:
        """GET /api/documents/{doc_id}/zotero-push?collection_key=
        推送框：可选集合、文件会移到哪、这篇的推送记录。"""
        result = await api_of(request).push_preview(
            request.match_info["doc_id"], request.query.get("collection_key") or "")
        return web.json_response(result)

    async def push(request: web.Request) -> web.Response:
        """POST /api/documents/{doc_id}/zotero-push {collection_key, filename}
        推送（移交）一篇；Zotero 写入失败时返回 failed 记录，文件已移回。"""
        body = await _json_body(request)
        key = str(body.get("collection_key") or "").strip()
        if not key:
            return _error("collection_key is required", 400)
        try:
            result = await api_of(request).push_document(
                request.match_info["doc_id"], key, str(body.get("filename") or ""))
        except ValueError as exc:
            return _error(str(exc), 400)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        return web.json_response(result)

    # ── 备份与迁移（.kaarch）：不进 AI 助手的白名单（含密钥与整库）──
    async def kaarch_export(request: web.Request) -> web.Response:
        """POST /api/kaarch/export {folder, parts, passphrase} — 后台导出，返回任务。"""
        body = await _json_body(request)
        parts = body.get("parts") if isinstance(body.get("parts"), list) else []
        try:
            job = api_of(request).kaarch_export(str(body.get("folder") or ""),
                                                [str(p) for p in parts],
                                                str(body.get("passphrase") or ""), app_version)
        except ValueError as exc:
            return _error(str(exc), 400)
        except RuntimeError as exc:
            return _error(str(exc), 409)
        return web.json_response(job)

    async def kaarch_inspect(request: web.Request) -> web.Response:
        """POST /api/kaarch/inspect {path} — 只读：文件里有什么、与本机的差别。"""
        path = str((await _json_body(request)).get("path") or "").strip()
        if not path:
            return _error("path is required", 400)
        try:
            return web.json_response(await api_of(request).kaarch_inspect(path))
        except ValueError as exc:
            return _error(str(exc), 400)

    async def kaarch_import(request: web.Request) -> web.Response:
        """POST /api/kaarch/import {path, mode: overwrite|merge, passphrase} — 后台导入。"""
        body = await _json_body(request)
        try:
            job = api_of(request).kaarch_import(str(body.get("path") or ""),
                                                str(body.get("mode") or ""),
                                                str(body.get("passphrase") or ""))
        except ValueError as exc:
            return _error(str(exc), 400)
        except RuntimeError as exc:
            return _error(str(exc), 409)
        return web.json_response(job)

    async def kaarch_job(request: web.Request) -> web.Response:
        """GET /api/kaarch/job — 最近一次导出 / 导入的进度与结果；没有为 null。"""
        try:
            return web.json_response(api_of(request).kaarch_job())
        except RuntimeError as exc:
            return _error(str(exc), 503)

    app.router.add_post("/api/kaarch/export", kaarch_export)
    app.router.add_post("/api/kaarch/inspect", kaarch_inspect)
    app.router.add_post("/api/kaarch/import", kaarch_import)
    app.router.add_get("/api/kaarch/job", kaarch_job)
    app.router.add_get("/api/external/captures/{capture_id}/sources/{ord}/import", import_preview)
    app.router.add_post("/api/external/captures/{capture_id}/sources/{ord}/import", import_source)
    app.router.add_get("/api/imports", imports_list)
    app.router.add_post("/api/imports/{import_id}/dismiss", import_dismiss)
    app.router.add_get("/api/documents/{doc_id}/import-origin", import_origin)
    app.router.add_get("/api/documents/{doc_id}/zotero-push", push_preview)
    app.router.add_post("/api/documents/{doc_id}/zotero-push", push)


__all__ = ["register_library_io_routes"]
