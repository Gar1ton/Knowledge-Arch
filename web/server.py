"""HTTP 路由（见 web/README.md 与 ../ARCHITECTURE.md §7）。

只做 HTTP↔业务的翻译：解析请求 → 调 `kacore/api` → 序列化响应。零业务逻辑。
服务只监听 127.0.0.1，只接受桌面壳窗口的请求：
- 鉴权：每次启动随机生成的 token，经 `Authorization: Bearer <token>` 携带（GET 下载类请求
  可退而用 `?access_token=`，因为浏览器导航无法附带请求头）。没有登录页与会话。
  另有可选的 agent token（AI 助手用，见 web/agent_routes.py）：只能访问白名单接口、不能写机密。
- CORS：只放行桌面壳窗口的 Origin（`tauri://localhost` 与 `http://tauri.localhost`），
  其余带 Origin 的请求一律 403；开发期经 build_app 的 allowed_origins 追加。
界面静态文件由桌面壳从安装包加载，本服务不托管。

依赖经 build_app 注入（api 门面 + token），自身不构造依赖。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging as _logging
import secrets
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from aiohttp import web

from web.agent_routes import (
    after_setting_change,
    authorize_agent,
    guard_secret_fields,
    guard_secret_setting,
)

if TYPE_CHECKING:
    from kacore.api import KnowledgeRepositoryApi
    from kacore.log_capture import MemoryLogHandler
    from web.agent_routes import AgentAccessLike

# ── 常量 ────────────────────────────────────────────────────────

# 桌面壳窗口的 Origin：macOS/Linux 为 tauri://localhost，Windows（WebView2）为 http://tauri.localhost。
DEFAULT_ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost")
ACCESS_TOKEN_QUERY = "access_token"
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 上传上限：超出返回 413，防内存/磁盘耗尽
_UPLOAD_CHUNK_BYTES = 1 << 20  # 流式读块大小：内存占用 O(chunk) 而非 O(file)
_API_PREFIX = "/api/"
_API_KEY = web.AppKey("api", object)
_UPLOAD_DIR_KEY = web.AppKey("upload_dir", Path)
_TOKEN_KEY = web.AppKey("token", str)
_ORIGINS_KEY = web.AppKey("allowed_origins", frozenset)
_SHUTDOWN_KEY = web.AppKey("shutdown_callback", object)
_CORS_ALLOW_HEADERS = "Authorization, Content-Type"
_CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_LOG_HANDLER_KEY = web.AppKey("log_handler", object)


# ── 中间件 ──────────────────────────────────────────────────────

_mw_logger = _logging.getLogger("KRWebServer")
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SLOW_REQUEST_SECONDS = 2.0
_LOG_ENDPOINTS = frozenset({"/api/logs", "/api/logs/events"})


def _canonical_route(request: web.Request) -> str:
    """返回不含用户实参的路由模板，避免日志泄露标题、路径或查询参数。"""
    try:
        resource = request.match_info.route.resource
        canonical = getattr(resource, "canonical", "")
        if canonical:
            return str(canonical)
    except (AttributeError, RuntimeError):
        pass
    return request.path


def _request_category(route: str) -> str:
    if any(part in route for part in ("rebuild-index", "search")):
        return "retrieval"
    if "/graph" in route:
        return "graph"
    if any(part in route for part in ("zotero", "notion", "backup", "restore", "sync")):
        return "sync"
    if any(part in route for part in ("documents", "collections")):
        return "ingest"
    if any(part in route for part in ("ask", "research")):
        return "llm"
    return "web"


@web.middleware
async def _request_observability_middleware(
    request: web.Request, handler: web.Handler
) -> web.StreamResponse:
    """直接记录 Web 请求时间线，不依赖宿主 logging 的 logger 配置。"""
    started = time.monotonic()
    request_id = secrets.token_hex(4)
    route = _canonical_route(request)
    category = _request_category(route)
    log_handler = cast("MemoryLogHandler | None", request.app.get(_LOG_HANDLER_KEY))
    is_api = request.path.startswith(_API_PREFIX)
    is_mutating = request.method in _MUTATING_METHODS and request.path not in _LOG_ENDPOINTS
    base_metadata = {"request_id": request_id, "method": request.method, "route": route}
    if log_handler is not None and is_mutating:
        log_handler.add_event(
            source="web",
            category=category,
            operation="http_request",
            status="started",
            msg=f"Web request started: {request.method} {route}",
            metadata=base_metadata,
        )
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if log_handler is not None and is_api and request.path not in _LOG_ENDPOINTS:
            level = "ERROR" if exc.status >= 500 else "WARNING"
            log_handler.add_event(
                source="web",
                category=category,
                operation="http_request",
                status="error" if exc.status >= 500 else "warning",
                level=level,
                msg=f"Web request finished: {request.method} {route} → HTTP {exc.status}",
                metadata={**base_metadata, "http_status": exc.status, "elapsed_ms": elapsed_ms},
            )
        exc.headers["X-KA-Request-ID"] = request_id
        raise
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    response.headers["X-KA-Request-ID"] = request_id
    should_log = (
        is_api
        and request.path not in _LOG_ENDPOINTS
        and (is_mutating or response.status >= 400 or elapsed_ms >= _SLOW_REQUEST_SECONDS * 1000)
    )
    if log_handler is not None and should_log:
        level = (
            "ERROR" if response.status >= 500
            else "WARNING" if response.status >= 400
            else "INFO"
        )
        status = (
            "error" if response.status >= 500
            else "warning" if response.status >= 400
            else "ok"
        )
        log_handler.add_event(
            source="web",
            category=category,
            operation="http_request",
            status=status,
            level=level,
            msg=f"Web request finished: {request.method} {route} → HTTP {response.status}",
            metadata={**base_metadata, "http_status": response.status, "elapsed_ms": elapsed_ms},
        )
    return response


@web.middleware
async def _error_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    """将所有未捕获异常转为 JSON 500，确保 API 始终返回结构化响应。"""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        _mw_logger.error(
            "Unhandled error [%s %s]: %s",
            request.method,
            request.path,
            exc,
            exc_info=True,
        )
        return web.json_response({"error": str(exc)}, status=500)


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": _CORS_ALLOW_HEADERS,
        "Access-Control-Allow-Methods": _CORS_ALLOW_METHODS,
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


@web.middleware
async def _cors_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    """只放行桌面壳窗口的 Origin；预检请求在鉴权之前应答（预检本身不带 Authorization）。

    不带 Origin 的请求（桌面壳原生侧 / 本机脚本）不经 CORS 判定，交由 token 鉴权。
    """
    origin = request.headers.get("Origin")
    if origin is None:
        return await handler(request)
    if origin not in request.app[_ORIGINS_KEY]:
        return web.json_response({"error": "origin not allowed"}, status=403)
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(origin))
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(_cors_headers(origin))
        raise
    response.headers.update(_cors_headers(origin))
    return response


def _request_token(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    if request.method == "GET":
        return request.query.get(ACCESS_TOKEN_QUERY, "")
    return ""


@web.middleware
async def _auth_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    """保护全部 /api/*：token 用常数时间比较，避免逐字符短路的定时侧信道。

    界面 token 放行全部接口；agent token 只放行白名单（authorize_agent）。
    """
    if request.path.startswith(_API_PREFIX):
        token = _request_token(request)
        if not token:
            return web.json_response({"error": "unauthorized"}, status=401)
        if not secrets.compare_digest(token, request.app[_TOKEN_KEY]):
            try:
                denied = authorize_agent(request, token)
            except KeyError:
                return web.json_response({"error": "unauthorized"}, status=401)
            if denied is not None:
                return denied
    return await handler(request)


# ── 路由处理 ────────────────────────────────────────────────────


def _api(request: web.Request) -> KnowledgeRepositoryApi:
    """取当前业务门面。服务软重启期间门面尚未重建，返回 503 让前端稍后重试。"""
    api = request.app[_API_KEY]()  # type: ignore[operator]
    if api is None:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "service is restarting"}),
            content_type="application/json",
        )
    return cast("KnowledgeRepositoryApi", api)


def _parse_int(raw: object, name: str, default: int, lo: int, hi: int) -> int:
    """解析整数参数：缺省用 default，非法值 400（JSON 体），越界夹取到 [lo, hi]。"""
    if raw is None or raw == "":
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"invalid {name}: expected integer"}),
            content_type="application/json",
        ) from None
    return max(lo, min(value, hi))


def _query_int(request: web.Request, name: str, default: int, lo: int, hi: int) -> int:
    return _parse_int(request.query.get(name), name, default, lo, hi)


def _query_flag(request: web.Request, name: str) -> bool:
    """解析布尔查询参数（`1` / `true` / `yes`，大小写不敏感）；缺省为 False。

    显式确认类的 flag 必须「默认关」：拼错值只会退回未确认，绝不会意外放行门禁。
    """
    return str(request.query.get(name) or "").strip().lower() in {"1", "true", "yes"}


# 字符偏移/长度类查询参数的解析上界，纯粹用来挡住荒谬输入，不表达任何业务上限。
_CHAR_OFFSET_CEILING = 2_000_000_000


def _fulltext_confirmation_payload(exc: Any) -> dict[str, Any]:
    """把全文确认门异常拍平成 409 响应体（`/api/ask` 与文档分页读共用同一形状）。

    刻意把展示所需的数字全带上：调用方据此直接渲染确认界面，不必再打一次 estimate 请求。
    阈值也一并回传——前端与 skill 因此不需要各自复制 60000 这个字面量。
    """
    return {
        "status": "fulltext_confirmation_required",
        "message": str(exc),
        "doc_id": exc.doc_id,
        "title": exc.title,
        "total_chars": exc.total_chars,
        "requested_chars": exc.requested_chars,
        "threshold_chars": exc.threshold_chars,
        "estimated_tokens": exc.estimated_tokens,
    }


async def handle_health(request: web.Request) -> web.Response:
    """存活探测：桌面壳在 KA_READY 之后与重启服务后轮询它。"""
    return web.json_response({"status": "ok"})


async def handle_shutdown(request: web.Request) -> web.Response:
    """请求后端有序退出（停定时任务 → 刷盘 Milvus → 关库）；实际退出由进程薄壳完成。"""
    callback = request.app[_SHUTDOWN_KEY]
    if not callable(callback):
        return web.json_response({"error": "shutdown is not supported here"}, status=501)
    callback()
    return web.json_response({"status": "shutting_down"}, status=202)


async def handle_list_collections(request: web.Request) -> web.Response:
    cols = await _api(request).list_collections()
    return web.json_response([_collection_dict(c) for c in cols])


async def handle_create_collection(request: web.Request) -> web.Response:
    from kacore.api import ReadOnlyError

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    description = body.get("description", "")
    parent_key = (body.get("parent_key") or "").strip()
    try:
        coll_key = await _api(request).create_subcollection(
            name, parent_key=parent_key, description=description
        )
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response(
        {"name": name, "description": description, "parent_key": parent_key, "coll_key": coll_key}
    )


async def handle_delete_collection(request: web.Request) -> web.Response:
    from kacore.api import ReadOnlyError

    try:
        ok = await _api(request).delete_collection(request.match_info["name"])
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_patch_collection(request: web.Request) -> web.Response:
    """按 coll_key 重命名 / 移动本地集合。body: {name?, parent_key?}。"""
    from kacore.api import ReadOnlyError

    coll_key = request.match_info["coll_key"]
    body = await request.json()
    api = _api(request)
    try:
        if "name" in body and body["name"] is not None:
            await api.rename_collection(coll_key, str(body["name"]))
        if "parent_key" in body:
            await api.move_collection(coll_key, str(body.get("parent_key") or ""))
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response({"ok": True})


async def handle_delete_collection_by_key(request: web.Request) -> web.Response:
    from kacore.api import ReadOnlyError

    try:
        ok = await _api(request).delete_collection_by_key(request.match_info["coll_key"])
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_list_documents(request: web.Request) -> web.Response:
    collection = request.query.get("collection") or None
    collection_key = request.query.get("collection_key") or None
    tag = request.query.get("tag") or None
    api = _api(request)
    if collection_key is not None:
        # DocumentsPanel 选中集合：仅本级文档（不递归后代）。
        docs = await api.list_documents_by_collection_key(collection_key, descendants=False)
        if tag is not None:
            docs = [d for d in docs if tag in d.tags]
    else:
        docs = await api.list_documents(collection=collection, tag=tag)
    return web.json_response([await _document_dict(api, d) for d in docs])


async def handle_upload_document(request: web.Request) -> web.Response:
    """multipart 上传：流式暂存（增量 sha256，内存 O(chunk)）→ 摄入插件托管原件库。"""
    reader = await request.multipart()
    collection = "default"
    tags: list[str] = []
    filename = "upload.bin"
    content_type = "application/octet-stream"
    upload_dir = request.app[_UPLOAD_DIR_KEY]
    upload_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = upload_dir / f".upload_{secrets.token_hex(8)}.part"
    hasher = hashlib.sha256()
    size_bytes = 0
    try:
        async for part in reader:
            if part.name == "file":
                # Path().name：剥掉客户端可控的路径分量，杜绝目录穿越。
                filename = Path(part.filename or filename).name or filename
                content_type = part.headers.get("Content-Type", content_type)
                with tmp_path.open("wb") as fh:
                    while True:
                        chunk = await part.read_chunk(_UPLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        size_bytes += len(chunk)
                        if size_bytes > _MAX_UPLOAD_BYTES:
                            max_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
                            return web.json_response(
                                {"error": f"file too large (max {max_mb} MB)"}, status=413
                            )
                        hasher.update(chunk)
                        fh.write(chunk)
            elif part.name == "collection":
                collection = (await part.text()).strip() or "default"
            elif part.name == "tags":
                tags = [t.strip() for t in (await part.text()).split(",") if t.strip()]
        if size_bytes == 0:
            return web.json_response({"error": "empty file"}, status=400)

        _mw_logger.info("Upload: file=%r size=%d collection=%r", filename, size_bytes, collection)
        content_hash = hasher.hexdigest()
        dest = upload_dir / f"{content_hash[:16]}_{filename}"
        tmp_path.replace(dest)
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        doc_id = await _api(request).register_document(
            title=filename,
            file_path=str(dest),
            content_type=content_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            collection=collection,
            tags=tags,
        )
    except Exception as exc:
        dest.unlink(missing_ok=True)
        _mw_logger.error("Upload failed for %r: %s", filename, exc, exc_info=True)
        return web.json_response({"error": str(exc)}, status=500)
    doc = await _api(request).get_document(doc_id)
    if doc is None:
        dest.unlink(missing_ok=True)
        return web.json_response({"error": "document registration failed"}, status=500)
    try:
        if Path(doc.file_path).resolve() != dest.resolve():
            dest.unlink(missing_ok=True)
    except OSError as exc:
        _mw_logger.warning("Failed to remove upload staging file %s: %s", dest, exc)
    return web.json_response(await _document_dict(_api(request), doc))


async def handle_classify_document(request: web.Request) -> web.Response:
    from kacore.api import ReadOnlyError

    body = await request.json()
    tags = body.get("tags")
    try:
        ok = await _api(request).classify_document(
            request.match_info["doc_id"],
            collection=body.get("collection"),
            tags=[str(t) for t in tags] if isinstance(tags, list) else None,
        )
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    if not ok:
        return web.json_response({"error": "document not found"}, status=404)
    doc = await _api(request).get_document(request.match_info["doc_id"])
    if doc is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(await _document_dict(_api(request), doc))


async def handle_document_meta_patch(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    body = await request.json()
    doc = await _api(request).update_document_meta(doc_id, body)
    if doc is None:
        return web.json_response({"error": "not found or not editable"}, status=404)
    return web.json_response(await _document_dict(_api(request), doc))


async def handle_delete_document(request: web.Request) -> web.Response:
    from kacore.api import ReadOnlyError

    doc_id = request.match_info["doc_id"]
    _mw_logger.info("Delete document: doc_id=%s", doc_id)
    try:
        ok = await _api(request).delete_document(doc_id)
    except ReadOnlyError as exc:
        return web.json_response({"status": "read_only", "message": str(exc)}, status=403)
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_reprocess_cleaning(request: web.Request) -> web.Response:
    """手动维护入口；默认只预览，复用已有鉴权与错误处理。"""
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    dry_run = body.get("dry_run", True)
    if not isinstance(dry_run, bool):
        return web.json_response({"error": "dry_run must be a boolean"}, status=400)
    return web.json_response(await _api(request).reprocess_documents_with_stale_cleaning(
        dry_run=dry_run,
    ))


async def handle_document_footnotes(request: web.Request) -> web.Response:
    notes = await _api(request).get_document_footnotes(request.match_info["doc_id"])
    if notes is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(notes)


async def handle_reextract_document(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    _mw_logger.info("Re-extract document: doc_id=%s", doc_id)
    try:
        result = await _api(request).reextract_document(doc_id)
    except FileNotFoundError as exc:
        return web.json_response({"status": "not_found", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "unsupported", "message": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)
    return web.json_response({"status": "ok", **result})


async def handle_document_content(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    fmt = (request.query.get("format") or "md").lower()
    if fmt != "md":
        return web.json_response(
            {"error": "only format=md is supported; use /raw for pdf"},
            status=400,
        )
    try:
        content = await _api(request).get_document_markdown_content(doc_id)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    if content is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.Response(text=content, content_type="text/markdown", charset="utf-8")


async def handle_document_content_page(request: web.Request) -> web.Response:
    """GET 文档 Markdown 的单个字符页；供具备明确阅读意图的 agent 使用。

    v1.1.1：`max_chars` 不再有 40000 硬上限（旧实现是**静默夹取**，请求 6 万字会被悄悄砍到
    4 万，调用方毫不知情）。`max_chars=0` 表示读到文末；单次返回量超阈值且无 `confirmed`
    时返回 409，形状与 `/api/ask` 的全文确认门完全一致。
    """
    from kacore.api import FullTextConfirmationRequiredError, FullTextDocumentUnavailableError

    doc_id = request.match_info["doc_id"]
    start = _query_int(request, "start", 0, 0, _CHAR_OFFSET_CEILING)
    max_chars = _query_int(request, "max_chars", 12000, 0, _CHAR_OFFSET_CEILING)
    try:
        result = await _api(request).get_document_markdown_page(
            doc_id,
            start=start,
            max_chars=max_chars,
            confirmed=_query_flag(request, "confirmed"),
        )
    except FullTextConfirmationRequiredError as exc:
        return web.json_response(_fulltext_confirmation_payload(exc), status=409)
    except FullTextDocumentUnavailableError as exc:
        return web.json_response({"error": str(exc), "reason": exc.reason}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if result is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(result)


async def handle_document_chunks(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    doc = await _api(request).get_document(doc_id)
    if doc is None:
        return web.json_response({"error": "document not found"}, status=404)
    chunks = await _api(request).list_document_chunks(doc_id)
    return web.json_response([_chunk_dict(c) for c in chunks])


async def handle_document_annotations(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    annotations = await _api(request).list_document_annotations(doc_id)
    if annotations is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(annotations)


async def handle_document_notes_get(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    notes = await _api(request).list_document_notes(doc_id)
    if notes is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(notes)


async def handle_document_notes_post(request: web.Request) -> web.Response:
    doc_id = request.match_info["doc_id"]
    body = await request.json() if request.can_read_body else {}
    content = str(body.get("content") or body.get("body") or "").strip()
    if not content:
        return web.json_response({"error": "content required"}, status=400)
    chat_message_id = body.get("chat_message_id")
    note = await _api(request).create_document_note(
        doc_id,
        content,
        linked=bool(body.get("linked") or False),
        source=str(body.get("source") or "manual"),
        chat_conversation_id=str(body.get("chat_conversation_id") or ""),
        chat_message_id=chat_message_id if isinstance(chat_message_id, int) else None,
    )
    if note is None:
        return web.json_response({"error": "document not found"}, status=404)
    return web.json_response(note, status=201)


async def handle_collection_notes_get(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    notes = await _api(request).list_collection_notes(name)
    if notes is None:
        return web.json_response({"error": "collection not found"}, status=404)
    return web.json_response(notes)


async def handle_collection_notes_post(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    body = await request.json() if request.can_read_body else {}
    content = str(body.get("content") or body.get("body") or "").strip()
    if not content:
        return web.json_response({"error": "content required"}, status=400)
    chat_message_id = body.get("chat_message_id")
    note = await _api(request).create_collection_note(
        name,
        content,
        linked=bool(body.get("linked") or False),
        source=str(body.get("source") or "manual"),
        chat_conversation_id=str(body.get("chat_conversation_id") or ""),
        chat_message_id=chat_message_id if isinstance(chat_message_id, int) else None,
    )
    if note is None:
        return web.json_response({"error": "collection not found"}, status=404)
    return web.json_response(note, status=201)


async def handle_download_document(request: web.Request) -> web.StreamResponse:
    doc_id = request.match_info["doc_id"]
    doc = await _api(request).get_document(doc_id)
    if doc is None:
        return web.json_response({"error": "document not found"}, status=404)
    file_path = Path(doc.file_path)
    if not file_path.is_file():
        return web.json_response({"error": "file not found on disk"}, status=404)
    disposition = request.query.get("disposition")
    disposition_type = "inline" if disposition == "inline" else "attachment"
    # 清洗引号与 CR/LF：title 可含任意字符，防 Content-Disposition 头注入/非法头值。
    filename = (doc.title or file_path.name).replace('"', "").replace("\r", " ").replace("\n", " ")
    return web.FileResponse(
        file_path,
        headers={
            "Content-Disposition": f'{disposition_type}; filename="{filename}"',
        },
    )


async def handle_kb_collections(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).list_kb_collections())


async def handle_kb_search(request: web.Request) -> web.Response:
    collection = request.query.get("collection", "")
    query = request.query.get("q", "")
    top_k = _query_int(request, "top_k", 5, 1, 50)
    window = _query_int(request, "window", 2, 0, 10)
    chunks = await _api(request).search_kb(
        collection,
        query,
        top_k,
        scope_type=request.query.get("scope_type", ""),
        scope_key=request.query.get("scope_key", ""),
        scope_library_id=request.query.get("scope_library_id", ""),
    )
    result = []
    for c in chunks:
        ctx = await _api(request).get_chunk_context(c.doc_id, c.chunk_id, window)
        entry = _chunk_dict(c)
        entry["context_before"] = ctx["context_before"]
        entry["context_after"] = ctx["context_after"]
        result.append(entry)
    return web.json_response(result)


async def handle_chunk_context(request: web.Request) -> web.Response:
    doc_id = request.query.get("doc_id", "")
    chunk_id = request.query.get("chunk_id", "")
    window = _query_int(request, "window", 2, 0, 10)
    if not doc_id or not chunk_id:
        return web.json_response({"error": "doc_id and chunk_id required"}, status=400)
    ctx = await _api(request).get_chunk_context(doc_id, chunk_id, window)
    return web.json_response(ctx)


# ── 预留端口路由（Reserved）──────────────────────────────────────
#
# 这些路由对应 kacore/api 的预留方法。现在就在 URL 层把「插座」装好，前端据此预留入口。
# 统一用 _reserved() 包裹：方法已实现则正常返回，未实现（NotImplementedError）则回 501 +
# available_in，使前端可显示「将在 vX.Y.0 接入」而无需改动布局。


async def _reserved(coro, available_in: str) -> web.Response:
    """执行预留方法；NotImplementedError → 501 + 结构化提示，其它异常照常上抛。"""
    try:
        result = await coro
    except NotImplementedError as exc:
        return web.json_response(
            {
                "status": "reserved",
                "reserved": True,
                "available_in": available_in,
                "detail": str(exc),
            },
            status=501,
        )
    return web.json_response(result)


async def handle_sync(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    target = request.match_info.get("target", "all")
    doc_ids = body.get("doc_ids") if isinstance(body, dict) else None
    _mw_logger.info("Sync requested: target=%s", target)
    return await _reserved(_api(request).sync_documents(target, doc_ids), "v0.3.0 / v0.4.0")


async def handle_notion_init(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    parent_page_id = body.get("parent_page_id") if isinstance(body, dict) else None
    database_title = body.get("database_title") if isinstance(body, dict) else None
    result = await _api(request).initialize_notion_database(parent_page_id, database_title)
    status = 503 if result.get("status") == "unavailable" else 200
    return web.json_response(result, status=status)


async def handle_notion_status(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).get_notion_push_status())


async def handle_notion_push_note(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)
    content = str(body.get("content", ""))
    tags = body.get("tags") if isinstance(body.get("tags"), list) else []
    citations = body.get("citations") if isinstance(body.get("citations"), list) else []
    result = await _api(request).push_note_to_notion(
        content,
        title=str(body.get("title", "")),
        tags=[str(t) for t in tags],
        citations=[str(c) for c in citations],
        source=str(body.get("source", "ask")),
        keep_local=bool(body.get("keep_local", False)),
    )
    status = 503 if result.get("status") == "unavailable" else 200
    return web.json_response(result, status=status)


async def handle_effective_config(request: web.Request) -> web.Response:
    return await _reserved(_api(request).get_effective_config(), "v0.8.0")


async def handle_update_config(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)
    section = body.get("section")
    key = body.get("key")
    value = body.get("value")
    if not section or not key:
        return web.json_response(
            {"status": "error", "message": "Missing section or key"}, status=400
        )

    _mw_logger.info("Config update: section=%s key=%s", section, key)

    async def _update():
        return await _api(request).update_config_value(section, key, value)

    try:
        resp = await _reserved(_update(), "v0.10.0")
        _mw_logger.info("Config update success: %s.%s", section, key)
        return resp
    except ValueError as exc:
        _mw_logger.warning("Config update rejected [%s.%s]: %s", section, key, exc)
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


# ── 设置 / 模型供应商 ───────────────────────────────────────────


async def _json_body(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _settings_error(exc: Exception) -> web.Response:
    """ValueError（含校验失败）→ 400；KeyError（连接不存在）→ 404；未装配 → 501。"""
    if isinstance(exc, KeyError):
        return web.json_response({"status": "error", "message": str(exc.args[0])}, status=404)
    if isinstance(exc, ValueError):
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response({"status": "error", "message": str(exc)}, status=501)


async def handle_settings_schema(request: web.Request) -> web.Response:
    try:
        return web.json_response(_api(request).get_settings_schema(request.query.get("home")))
    except RuntimeError as exc:
        return _settings_error(exc)


async def handle_settings_patch(request: web.Request) -> web.Response:
    body = await _json_body(request)
    if body is None or not isinstance(body.get("key"), str):
        return web.json_response({"status": "error", "message": "key required"}, status=400)
    denied = guard_secret_setting(request, body["key"])
    if denied is not None:
        return denied
    try:
        result = await _api(request).update_setting(body["key"], body.get("value"))
    except (ValueError, RuntimeError) as exc:
        return _settings_error(exc)
    after_setting_change(request, body["key"], body.get("value"))
    return web.json_response(result)


async def handle_providers_list(request: web.Request) -> web.Response:
    try:
        return web.json_response(_api(request).list_providers())
    except RuntimeError as exc:
        return _settings_error(exc)


async def handle_providers_create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    if body is None:
        return web.json_response({"status": "error", "message": "invalid JSON body"}, status=400)
    denied = guard_secret_fields(request, body)
    if denied is not None:
        return denied
    try:
        return web.json_response(_api(request).create_provider(body), status=201)
    except (ValueError, TypeError, RuntimeError) as exc:
        return _settings_error(ValueError(str(exc)) if isinstance(exc, TypeError) else exc)


async def handle_providers_patch(request: web.Request) -> web.Response:
    body = await _json_body(request)
    if body is None:
        return web.json_response({"status": "error", "message": "invalid JSON body"}, status=400)
    denied = guard_secret_fields(request, body)
    if denied is not None:
        return denied
    try:
        return web.json_response(
            await _api(request).update_provider(request.match_info["provider_id"], body)
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        return _settings_error(exc)


async def handle_providers_delete(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            _api(request).delete_provider(request.match_info["provider_id"])
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        return _settings_error(exc)


async def handle_providers_test(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _api(request).test_provider(request.match_info["provider_id"])
        )
    except (KeyError, RuntimeError) as exc:
        return _settings_error(exc)


async def handle_providers_models(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _api(request).refresh_provider_models(request.match_info["provider_id"])
        )
    except (KeyError, RuntimeError) as exc:
        return _settings_error(exc)


async def handle_service_restart(request: web.Request) -> web.Response:
    _mw_logger.info("Plugin soft restart requested")
    try:
        result = await _api(request).restart_service()
        return web.json_response(result)
    except Exception as exc:  # noqa: BLE001
        _mw_logger.error("Service restart failed to schedule: %s", exc, exc_info=True)
        return web.json_response({"status": "error", "message": str(exc)}, status=503)


async def handle_rebuild_index_pending(request: web.Request) -> web.Response:
    _mw_logger.info("Index rebuild requested")
    try:
        # 后台触发即返回；进度由前端轮询 /api/documents/rebuild-index/active 获取。
        job = await _api(request).start_milvus_rebuild()
        return web.json_response({"status": "started", "job": job})
    except Exception as exc:
        _mw_logger.error("Index rebuild failed to start: %s", exc, exc_info=True)
        return web.json_response({"status": "error", "message": str(exc)}, status=503)


async def handle_milvus_build_active(request: web.Request) -> web.Response:
    result = _api(request).get_active_milvus_build_job()
    return web.json_response({"job": result})


async def handle_ingest_active(request: web.Request) -> web.Response:
    # 统一进度面板轮询：当前 running/error 的文档摄入任务（success/无任务 → null）。
    return web.json_response({"job": _api(request).get_active_ingest_job()})


async def handle_pending_reindex_count(request: web.Request) -> web.Response:
    count = await _api(request).get_pending_reindex_count()
    return web.json_response({"count": count})


async def handle_test_embedding(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    base_url = body.get("base_url", "https://api.openai.com/v1")
    model_name = body.get("model_name", "")
    result = await _api(request).test_embedding_connection(base_url, model_name)
    return web.json_response(result)


async def handle_sync_status(request: web.Request) -> web.Response:
    return await _reserved(_api(request).get_sync_status(), "v0.3.0")


async def handle_zotero_config(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).get_zotero_config())


async def handle_zotero_probe(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).probe_zotero_local())


async def handle_zotero_server_key_post(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"status": "error", "message": "invalid JSON"}, status=400)
    api_key = str(body.get("api_key") or body.get("key") or "").strip()
    if not api_key:
        return web.json_response(
            {"status": "error", "message": "api_key is required"},
            status=400,
        )
    try:
        return web.json_response(await _api(request).save_zotero_server_key(api_key))
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_zotero_server_key_delete(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _api(request).delete_zotero_server_key())
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_zotero_pull(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    incremental = bool(body.get("incremental", True)) if isinstance(body, dict) else True
    try:
        return web.json_response(await _api(request).sync_zotero_pull(incremental=incremental))
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_zotero_status(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).get_zotero_sync_status())


async def handle_zotero_active(request: web.Request) -> web.Response:
    # 进度面板轮询：返回当前 running/partial/error 的同步任务快照（success/无任务 → null）。
    return web.json_response({"job": _api(request).get_active_zotero_sync_job()})


async def handle_notion_active(request: web.Request) -> web.Response:
    # 进度面板轮询：返回当前 running/partial/error 的 Notion 推送任务快照（无任务 → null）。
    return web.json_response({"job": _api(request).get_active_notion_sync_job()})


async def handle_backup(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    force = bool(body.get("force", False)) if isinstance(body, dict) else False
    return await _reserved(
        _api(request).backup_now(force=force, background=True), "v0.29.0"
    )


async def handle_restore(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    snapshot = body.get("snapshot") if isinstance(body, dict) else None
    auto_restart = bool(body.get("auto_restart", True)) if isinstance(body, dict) else True
    return await _reserved(
        _api(request).restore_from_backup(
            snapshot, auto_restart=auto_restart, background=True
        ),
        "v0.29.0",
    )


async def handle_r2_status(request: web.Request) -> web.Response:
    return web.json_response(await _api(request).get_r2_status())


async def handle_r2_job(request: web.Request) -> web.Response:
    return web.json_response({"job": _api(request).get_r2_job()})


async def handle_graph_build_estimate(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    collection = body.get("collection") if isinstance(body, dict) else None
    try:
        result = await _api(request).estimate_graph_build(collection)
        return web.json_response(result)
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_graph_build(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    collection = body.get("collection") if isinstance(body, dict) else None
    confirmed = bool(body.get("confirmed")) if isinstance(body, dict) else False
    _mw_logger.info("Graph build requested: collection=%r confirmed=%s", collection, confirmed)
    try:
        result = await _api(request).build_graph(collection, confirmed=confirmed)
        return web.json_response(result)
    except ValueError as exc:
        _mw_logger.warning("Graph build rejected: %s", exc)
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_codex_graph_build(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    collection = body.get("collection") if isinstance(body, dict) else None
    confirmed = bool(body.get("confirmed")) if isinstance(body, dict) else False
    try:
        return web.json_response(
            await _api(request).build_graph_with_codex(collection, confirmed=confirmed)
        )
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=409)


async def handle_codex_graph_next(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _api(request).get_next_codex_graph_task(request.match_info["job_id"])
        )
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_codex_graph_submit(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
        return web.json_response(
            {"status": "error", "message": "task_id and result object are required"},
            status=400,
        )
    task_id = str(body.get("task_id") or "")
    if not task_id:
        return web.json_response(
            {"status": "error", "message": "task_id and result object are required"},
            status=400,
        )
    try:
        return web.json_response(
            await _api(request).submit_codex_graph_task(
                request.match_info["job_id"], task_id, body["result"]
            )
        )
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    except Exception as exc:
        _mw_logger.error("Codex graph task submission failed: %s", exc, exc_info=True)
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_codex_graph_retry(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    task_id = str(body.get("task_id") or "") if isinstance(body, dict) else ""
    if not task_id:
        return web.json_response(
            {"status": "error", "message": "task_id is required"}, status=400
        )
    try:
        return web.json_response(
            await _api(request).retry_codex_graph_task(
                request.match_info["job_id"], task_id
            )
        )
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_zotero_account_change(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    change_id = str(body.get("change_id") or "") if isinstance(body, dict) else ""
    action = str(body.get("action") or "") if isinstance(body, dict) else ""
    try:
        return web.json_response(
            await _api(request).resolve_zotero_account_change(change_id, action)
        )
    except (ValueError, RuntimeError) as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    except Exception as exc:
        _mw_logger.error("Zotero account change failed: %s", exc, exc_info=True)
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_graph_build_job(request: web.Request) -> web.Response:
    result = await _api(request).get_graph_build_job(request.match_info["job_id"])
    if result is None:
        return web.json_response({"status": "error", "message": "job not found"}, status=404)
    return web.json_response(result)


async def handle_graph_build_active(request: web.Request) -> web.Response:
    result = await _api(request).get_active_build_job()
    return web.json_response({"job": result})


async def handle_graph_build_history(request: web.Request) -> web.Response:
    collection = request.rel_url.query.get("collection") or None
    result = await _api(request).get_build_job_history(collection)
    return web.json_response({"jobs": result})


async def handle_graph_build_pause(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    try:
        await _api(request).pause_build_job(job_id)
        return web.json_response({"status": "paused"})
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_graph_build_resume(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    try:
        await _api(request).resume_build_job(job_id)
        return web.json_response({"status": "resumed"})
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)


async def handle_graph_build_cancel(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    body = await request.json() if request.can_read_body else {}
    cleanup = bool(body.get("cleanup", True)) if isinstance(body, dict) else True
    try:
        result = await _api(request).cancel_build_job(job_id, cleanup=cleanup)
        return web.json_response(result)
    except KeyError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_graph_stats(request: web.Request) -> web.Response:
    """GET /api/graph/stats — 图谱摘要统计（实体数、关系数、涉及集合数）。"""
    try:
        result = await _api(request).get_graph_stats()
        return web.json_response(result)
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def handle_ask_progress(request: web.Request) -> web.Response:
    """GET /api/ask/progress/{cid} — 轮询指定对话的召回进度。"""
    cid = request.match_info.get("cid", "")
    progress = _api(request).get_ask_progress(cid)
    if progress is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(progress)


async def handle_chat_history_get(request: web.Request) -> web.Response:
    """GET /api/chat/history?conversation_id=<id> — 返回某会话的聊天记录。"""
    cid = request.query.get("conversation_id", "").strip()
    if not cid:
        return web.json_response({"error": "conversation_id required"}, status=400)
    messages = await _api(request).get_chat_history(cid)
    return web.json_response({"messages": messages})


async def handle_chat_history_clear(request: web.Request) -> web.Response:
    """DELETE /api/chat/history?conversation_id=<id> — 清除某会话的聊天记录。"""
    cid = request.query.get("conversation_id", "").strip()
    if not cid:
        return web.json_response({"error": "conversation_id required"}, status=400)
    preserve_locked = request.query.get("preserve_locked", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    await _api(request).clear_chat_history(cid, preserve_locked=preserve_locked)
    return web.json_response({"status": "ok"})


async def handle_chat_message_lock(request: web.Request) -> web.Response:
    """PATCH /api/chat/history/{convId}/messages/{msgIdx}/lock — 锁定/取消锁定消息。"""
    conv_id = request.match_info["conv_id"]
    try:
        msg_idx = int(request.match_info["msg_idx"])
    except ValueError:
        return web.json_response({"error": "msgIdx must be an integer"}, status=400)
    body = await request.json() if request.can_read_body else {}
    locked = bool(body.get("locked", True)) if isinstance(body, dict) else True
    message = await _api(request).set_chat_message_locked(conv_id, msg_idx, locked)
    if message is None:
        return web.json_response({"error": "message not found"}, status=404)
    return web.json_response(message)


async def handle_console_scope_state_get(request: web.Request) -> web.Response:
    scope_type = request.query.get("scope_type", "").strip()
    scope_key = request.query.get("scope_key", "").strip()
    if not scope_type or not scope_key:
        return web.json_response({"error": "scope_type and scope_key required"}, status=400)
    state = await _api(request).get_console_scope_state(scope_type, scope_key)
    if state is None:
        return web.json_response({"error": "scope state not found"}, status=404)
    return web.json_response(state)


async def handle_console_scope_state_upsert(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    scope_type = str(body.get("scope_type") or "").strip()
    scope_key = str(body.get("scope_key") or "").strip()
    if not scope_type or not scope_key:
        return web.json_response({"error": "scope_type and scope_key required"}, status=400)
    payload = body.get("payload")
    state = await _api(request).upsert_console_scope_state(
        scope_type,
        scope_key,
        selected_collection=str(body.get("selected_collection") or ""),
        selected_doc_id=str(body.get("selected_doc_id") or ""),
        note_doc_id=str(body.get("note_doc_id") or ""),
        right_panel=str(body.get("right_panel") or ""),
        reading_mode=str(body.get("reading_mode") or ""),
        payload=payload if isinstance(payload, dict) else {},
    )
    return web.json_response(state)


async def handle_system_info(request: web.Request) -> web.Response:
    """GET /api/system/info — 返回后端运行环境信息（调试面板用）。"""
    try:
        return web.json_response(_api(request).get_system_info())
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_model_runtime(request: web.Request) -> web.Response:
    """GET /api/system/models — 本地模型驻留状态 + 尽力而为的显存读数（模型面板轮询）。"""
    try:
        return web.json_response(await _api(request).get_model_runtime())
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_model_unload(request: web.Request) -> web.Response:
    """POST /api/system/models/unload — 立即卸载本地模型并清空显存缓存。

    body: `{"kinds": ["embedding", "rerank"]}`，缺省或空表示全部。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_kinds = body.get("kinds") if isinstance(body, dict) else None
    kinds = [str(k) for k in raw_kinds] if isinstance(raw_kinds, list) else None
    try:
        return web.json_response(await _api(request).unload_models(kinds))
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_fs_browse(request: web.Request) -> web.Response:
    """GET /api/fs/browse?path=<abs_path> — 列出指定绝对路径下的子目录（供文件夹选择器使用）。"""
    import os
    raw_path = request.query.get("path", "").strip()
    if not raw_path:
        raw_path = str(os.path.expanduser("~"))
    def _list_dirs(path: str) -> list[str]:
        entries = []
        with os.scandir(path) as it:
            for entry in sorted(it, key=lambda e: e.name.lower()):
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    entries.append(entry.name)
        return entries

    try:
        target = os.path.realpath(os.path.expanduser(raw_path))
        if not os.path.isdir(target):
            return web.json_response({"error": "not a directory"}, status=400)
        # to_thread：scandir 超大目录（如网络盘）是同步 IO，不能占住事件循环。
        entries = await asyncio.to_thread(_list_dirs, target)
        parent = str(os.path.dirname(target)) if target != os.path.dirname(target) else None
        return web.json_response({"path": target, "parent": parent, "dirs": entries})
    except PermissionError:
        return web.json_response({"error": "permission denied"}, status=403)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_list_local_models(request: web.Request) -> web.Response:
    """GET /api/models/local — 列出 HuggingFace 缓存中的本地模型。"""
    try:
        return web.json_response(_api(request).list_local_embedding_models())
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_delete_local_model(request: web.Request) -> web.Response:
    """DELETE /api/models/local/{name} — 删除本地缓存模型（name 经 URL 编码）。"""
    from urllib.parse import unquote

    raw = request.match_info.get("name", "")
    model_name = unquote(raw)
    try:
        result = _api(request).delete_local_embedding_model(model_name)
        return web.json_response(result)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_capabilities(request: web.Request) -> web.Response:
    """GET /api/capabilities — 数据流各环节状态 + 依赖状态 + 诊断（向导页唯一数据源）。"""
    try:
        return web.json_response(await _api(request).get_capabilities())
    except NotImplementedError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_dependencies(request: web.Request) -> web.Response:
    """GET /api/dependencies — 列出可选依赖的安装/版本状态。"""
    try:
        return web.json_response({"dependencies": _api(request).list_dependencies()})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_dependency_install(request: web.Request) -> web.Response:
    """POST /api/dependencies/install — 一键安装清单内依赖（仅白名单），输出转发到日志。"""
    body = await request.json() if request.can_read_body else {}
    package = body.get("package") if isinstance(body, dict) else None
    if not package:
        return web.json_response({"status": "error", "message": "Missing package"}, status=400)
    _mw_logger.info("Dependency install requested: %s", package)
    try:
        result = await _api(request).install_dependency(package)
        status = 200 if result.get("status") == "ok" else 500
        return web.json_response(result, status=status)
    except ValueError as exc:  # 白名单外包名
        _mw_logger.warning("Dependency install rejected: %s", exc)
        return web.json_response({"status": "error", "message": str(exc)}, status=400)


async def handle_dependency_recheck(request: web.Request) -> web.Response:
    """POST /api/dependencies/recheck — 清 import 缓存后重新探测并返回最新状态。"""
    try:
        return web.json_response(await _api(request).recheck_dependencies())
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_log_event(request: web.Request) -> web.Response:
    """POST /api/logs/events — 接收前端 toast 等非 logging 运行事件。"""
    handler = cast("MemoryLogHandler | None", request.app.get(_LOG_HANDLER_KEY))
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)
    if handler is not None:
        event_type = str(body.get("type") or "info")
        status = "error" if event_type == "error" else "ok" if event_type == "ok" else "info"
        level = "ERROR" if event_type == "error" else "INFO"
        handler.add_event(
            source="frontend",
            category="toast",
            operation="notify",
            status=status,
            level=level,
            msg=str(body.get("message") or ""),
            metadata={
                "route": str(body.get("route") or ""),
                "toast_type": event_type,
            },
        )
    return web.json_response({"status": "ok"})


async def handle_logs(request: web.Request) -> web.Response:
    """GET /api/logs?after=<float>&limit=<int> — 返回内存日志缓冲区中的最新日志行。"""
    handler = cast("MemoryLogHandler | None", request.app.get(_LOG_HANDLER_KEY))
    if handler is None:
        return web.json_response(
            {
                "lines": [],
                "server_ts": time.time(),
                "oldest_seq": 0,
                "latest_seq": 0,
                "dropped_count": 0,
            }
        )
    try:
        after_ts = float(request.query.get("after", "0"))
        after_seq_raw = request.query.get("after_seq")
        after_seq = int(after_seq_raw) if after_seq_raw is not None else None
        limit = min(int(request.query.get("limit", "200")), 2000)
        if after_seq is not None and after_seq < 0:
            raise ValueError
    except ValueError:
        return web.json_response({"error": "invalid params"}, status=400)
    batch = handler.get_batch(after_seq=after_seq, after_ts=after_ts, limit=limit)
    return web.json_response({**batch, "server_ts": time.time()})


async def handle_ask(request: web.Request) -> web.Response:
    """POST /api/ask — Ask Agent：检索 + LLM 答案生成。"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    question = (body.get("question") or "").strip()
    if not question:
        return web.json_response({"error": "question required"}, status=400)
    collection = body.get("collection") or None
    top_k = _parse_int(body.get("top_k"), "top_k", 6, 1, 20)
    conversation_id = body.get("conversation_id") or None
    retrieval_mode = body.get("retrieval_mode") or "default"
    use_english_retrieval = bool(body.get("use_english_retrieval") or False)
    use_context = bool(body.get("use_context") or False)
    answer_language = str(body.get("answer_language") or "auto")
    # doc_id / confirmed 服务于 retrieval_mode="fulltext"。前端从 v1.0.x 起就一直在发
    # doc_id，但这里从未读取过——全文检索因此始终跑不通（模式名也不在白名单里）。
    doc_id = str(body.get("doc_id") or "") or None
    confirmed = bool(body.get("confirmed") or False)
    from kacore.api import (
        AskTaskTimeoutError,
        FullTextConfirmationRequiredError,
        FullTextDocumentUnavailableError,
        FullTextGenerationFailedError,
        GraphMixedQueryError,
        LightRAGNotReadyError,
    )

    try:
        result = await _api(request).ask(
            question=question,
            collection=collection,
            top_k=top_k,
            conversation_id=conversation_id,
            retrieval_mode=retrieval_mode,
            use_english_retrieval=use_english_retrieval,
            use_context=use_context,
            answer_language=answer_language,
            doc_id=doc_id,
            confirmed=confirmed,
            scope_type=str(body.get("scope_type") or ""),
            scope_key=str(body.get("scope_key") or ""),
            scope_library_id=str(body.get("scope_library_id") or ""),
        )
    except FullTextConfirmationRequiredError as exc:
        # 409 不是失败，是一次询问：正文超过确认阈值，前端据此弹窗，用户同意后带
        # confirmed=true 重发同一请求。此时后端尚未调用 LLM、尚未写任何 chat_history。
        return web.json_response(_fulltext_confirmation_payload(exc), status=409)
    except FullTextDocumentUnavailableError as exc:
        return web.json_response(
            {
                "status": "fulltext_document_unavailable",
                "message": str(exc),
                "doc_id": exc.doc_id,
                "reason": exc.reason,
            },
            status=404,
        )
    except FullTextGenerationFailedError as exc:
        return web.json_response(
            {
                "status": "fulltext_generation_failed",
                "message": exc.reason,
                "doc_id": exc.doc_id,
                "total_chars": exc.total_chars,
            },
            status=502,
        )
    except LightRAGNotReadyError as exc:
        return web.json_response(
            {
                "status": "lightrag_not_ready",
                "message": exc.reason,
                "collection": exc.collection,
                "build_available": exc.build_available,
            },
            status=409,
        )
    except GraphMixedQueryError as exc:
        return web.json_response(
            {
                "status": f"{exc.mode}_failed",
                "message": exc.reason,
                "collection": exc.collection,
            },
            status=502,
        )
    except AskTaskTimeoutError as exc:
        # 202：请求仍在后台运行（未取消），不是失败——前端据此改为轮询
        # /api/ask/progress + /api/chat/history 找回迟到的答案，而不是弹错误后放弃。
        return web.json_response(
            {
                "status": "ask_task_timeout",
                "message": str(exc),
                "conversation_id": exc.conversation_id,
                "timeout_seconds": exc.timeout_seconds,
            },
            status=202,
        )
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    return web.json_response(result)


async def handle_ask_evidence(request: web.Request) -> web.Response:
    """POST /api/ask/evidence — Codex 分档召回证据；不调用插件 LLM。"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON object required"}, status=400)
    question = str(body.get("question") or "").strip()
    raw_queries = body.get("queries")
    if raw_queries is None:
        queries = None
    elif isinstance(raw_queries, list) and all(isinstance(item, str) for item in raw_queries):
        queries = list(raw_queries)
    else:
        return web.json_response({"error": "queries must be a list of strings"}, status=400)
    try:
        result = await _api(request).retrieve_agent_evidence(
            question=question,
            queries=queries,
            collection=str(body.get("collection") or "") or None,
            retrieval_mode=str(body.get("retrieval_mode") or "default"),
            round_number=_parse_int(body.get("round"), "round", 1, 1, 20),
            scope_type=str(body.get("scope_type") or ""),
            scope_key=str(body.get("scope_key") or ""),
            scope_library_id=str(body.get("scope_library_id") or ""),
        )
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"status": "unavailable", "message": str(exc)}, status=503)
    return web.json_response(result)


# ── 序列化 helper（domain → JSON-safe dict）─────────────────────


def _collection_dict(c: object) -> dict:
    from kacore.api import SYSTEM_COLLECTION_UNCATEGORIZED

    origin = getattr(c, "origin", None)
    origin_val = origin.value if origin is not None else "local"
    return {
        "name": c.name,  # type: ignore[attr-defined]
        "description": c.description,  # type: ignore[attr-defined]
        "is_system": c.name == SYSTEM_COLLECTION_UNCATEGORIZED,  # type: ignore[attr-defined]
        "origin": origin_val,
        "read_only": bool(getattr(c, "read_only", False)) or origin_val == "zotero",
        "zotero_collection_key": getattr(c, "zotero_collection_key", ""),
        "coll_key": getattr(c, "coll_key", ""),
        "parent_key": getattr(c, "parent_key", ""),
        "library_id": getattr(c, "library_id", "LOCAL"),
    }


_TEXT_EXTS = frozenset({"md", "markdown", "txt"})


def _document_ext(d: object) -> str:
    """原件类型（界面图标与是否提供 PDF 阅读模式）。

    本地上传的原件一律存成制品包里的 `original.pdf`，文本文件也是；浏览器给 .md 的 MIME
    又常是 octet-stream。所以这类文件看开头是否为 `%PDF-`，不是就按标题后缀，取不到记 txt。
    """
    path = Path(d.file_path)  # type: ignore[attr-defined]
    ext = path.suffix.lstrip(".").lower()
    if path.name != "original.pdf" or d.content_type == "application/pdf":  # type: ignore[attr-defined]
        return ext
    try:
        with path.open("rb") as fh:
            if fh.read(5) == b"%PDF-":
                return "pdf"
    except OSError:
        return ext
    title_ext = Path(d.title or "").suffix.lstrip(".").lower()  # type: ignore[attr-defined]
    return title_ext if title_ext in _TEXT_EXTS else "txt"


async def _document_dict(api: KnowledgeRepositoryApi, d: object) -> dict:
    chunks = await api.list_document_chunks(d.doc_id)  # type: ignore[attr-defined]
    lightrag_status = await api.get_lightrag_index_status(d.doc_id)  # type: ignore[attr-defined]
    updated_at = d.updated_at.isoformat() if d.updated_at else None  # type: ignore[attr-defined]
    filename = Path(d.file_path).name  # type: ignore[attr-defined]
    ext = _document_ext(d)
    origin = getattr(d, "origin", None)
    origin_val = origin.value if origin is not None else "local"
    lifecycle = getattr(d, "lifecycle_state", None)
    lifecycle_val = lifecycle.value if lifecycle is not None else "active"
    last_synced = getattr(d, "last_synced_at", None)
    last_synced_iso = last_synced.isoformat() if last_synced else None
    needs_reindex = getattr(d, "needs_reindex", False)
    # Milvus 覆盖指示：active + 有 chunk + 未待重索引（无 bug 时应全勾）。
    milvus_covered = bool(chunks) and not needs_reindex and lifecycle_val == "active"
    result = {
        "doc_id": d.doc_id,  # type: ignore[attr-defined]
        "title": d.title,  # type: ignore[attr-defined]
        "filename": filename,
        "content_type": d.content_type,  # type: ignore[attr-defined]
        "size_bytes": d.size_bytes,  # type: ignore[attr-defined]
        "size": d.size_bytes,  # type: ignore[attr-defined]
        "collection": d.collection,  # type: ignore[attr-defined]
        "tags": d.tags,  # type: ignore[attr-defined]
        "content_hash": d.content_hash,  # type: ignore[attr-defined]
        "chunks": len(chunks),
        "updated_at": updated_at,
        "updated": updated_at,
        "ext": ext,
        "needs_reindex": needs_reindex,
        "lightrag_index_status": lightrag_status,
        # ── 制品包 / 来源 / 三指示元数据 ──
        "origin": origin_val,
        "read_only": bool(getattr(d, "read_only", False)),
        "lifecycle_state": lifecycle_val,
        "library_id": getattr(d, "library_id", "LOCAL"),
        "zotero_item_key": getattr(d, "zotero_item_key", ""),
        "attachment_key": getattr(d, "attachment_key", ""),
        "last_synced_at": last_synced_iso,
        "milvus_covered": milvus_covered,
    }
    if origin_val == "zotero":
        meta = await api.get_zotero_item_meta(
            getattr(d, "library_id", ""), getattr(d, "zotero_item_key", "")
        )
        if meta:
            result["zotero_meta"] = meta
    elif origin_val == "local":
        local_meta = getattr(d, "local_meta", {}) or {}
        if local_meta:
            result["zotero_meta"] = local_meta
    return result


def _chunk_dict(c: object) -> dict:
    metadata = getattr(c, "metadata", {}) or {}
    return {
        "chunk_id": c.chunk_id,  # type: ignore[attr-defined]
        "doc_id": c.doc_id,  # type: ignore[attr-defined]
        "ordinal": c.ordinal,  # type: ignore[attr-defined]
        "text": c.text,  # type: ignore[attr-defined]
        "page": metadata.get("page_number") or metadata.get("page"),
        "metadata": metadata,
    }


# ── 应用装配 ────────────────────────────────────────────────────


def build_app(
    *,
    api: KnowledgeRepositoryApi | None = None,
    api_provider: Callable[[], KnowledgeRepositoryApi | None] | None = None,
    upload_dir: Path,
    token: str,
    allowed_origins: Iterable[str] = DEFAULT_ALLOWED_ORIGINS,
    shutdown_callback: Callable[[], None] | None = None,
    log_handler: MemoryLogHandler | None = None,
    agent_access: AgentAccessLike | None = None,
    app_version: str = "",
) -> web.Application:
    """构造 aiohttp 应用：注入业务门面、token 与 CORS 白名单。

    api 与 api_provider 二选一：固定门面（测试）或每次请求现取（进程薄壳——服务软重启会
    重建门面，HTTP 服务本身不重启）。token 为空时拒绝构造（避免无鉴权暴露）。
    shutdown_callback 为空时 /api/shutdown 返回 501。agent_access 为空时不认 agent token。
    """
    if not token:
        raise ValueError("an access token is required")
    if (api is None) == (api_provider is None):
        raise ValueError("exactly one of api / api_provider is required")
    if api_provider is None:
        fixed_api = api

        def api_provider() -> KnowledgeRepositoryApi | None:
            return fixed_api

    app = web.Application(
        middlewares=[
            _cors_middleware,
            _request_observability_middleware,
            _error_middleware,
            _auth_middleware,
        ]
    )
    app[_API_KEY] = api_provider
    app[_UPLOAD_DIR_KEY] = upload_dir
    app[_TOKEN_KEY] = token
    app[_ORIGINS_KEY] = frozenset(allowed_origins)
    app[_SHUTDOWN_KEY] = shutdown_callback

    from kacore.log_capture import install as _install_log_handler

    app[_LOG_HANDLER_KEY] = log_handler or _install_log_handler()

    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/shutdown", handle_shutdown)
    app.router.add_get("/api/collections", handle_list_collections)
    app.router.add_post("/api/collections", handle_create_collection)
    app.router.add_patch("/api/collections/by-key/{coll_key}", handle_patch_collection)
    app.router.add_delete(
        "/api/collections/by-key/{coll_key}", handle_delete_collection_by_key
    )
    app.router.add_delete("/api/collections/{name}", handle_delete_collection)
    app.router.add_get("/api/collections/{name}/notes", handle_collection_notes_get)
    app.router.add_post("/api/collections/{name}/notes", handle_collection_notes_post)
    app.router.add_get("/api/documents", handle_list_documents)
    app.router.add_post("/api/documents", handle_upload_document)
    app.router.add_patch("/api/documents/{doc_id}", handle_classify_document)
    app.router.add_patch("/api/documents/{doc_id}/meta", handle_document_meta_patch)
    app.router.add_delete("/api/documents/{doc_id}", handle_delete_document)
    app.router.add_post("/api/documents/reprocess-cleaning", handle_reprocess_cleaning)
    app.router.add_get("/api/documents/{doc_id}/footnotes", handle_document_footnotes)
    app.router.add_post("/api/documents/{doc_id}/reextract", handle_reextract_document)
    app.router.add_get("/api/documents/{doc_id}/content", handle_document_content)
    app.router.add_get("/api/documents/{doc_id}/content/page", handle_document_content_page)
    app.router.add_get("/api/documents/{doc_id}/chunks", handle_document_chunks)
    app.router.add_get("/api/documents/{doc_id}/annotations", handle_document_annotations)
    app.router.add_get("/api/documents/{doc_id}/notes", handle_document_notes_get)
    app.router.add_post("/api/documents/{doc_id}/notes", handle_document_notes_post)
    app.router.add_get("/api/documents/{doc_id}/raw", handle_download_document)
    app.router.add_get("/api/kb/collections", handle_kb_collections)
    app.router.add_get("/api/kb/search", handle_kb_search)
    app.router.add_get("/api/kb/chunk-context", handle_chunk_context)
    app.router.add_get("/api/config/effective", handle_effective_config)
    app.router.add_post("/api/config/update", handle_update_config)
    app.router.add_get("/api/settings/schema", handle_settings_schema)
    app.router.add_patch("/api/settings", handle_settings_patch)
    app.router.add_get("/api/providers", handle_providers_list)
    app.router.add_post("/api/providers", handle_providers_create)
    app.router.add_patch("/api/providers/{provider_id}", handle_providers_patch)
    app.router.add_delete("/api/providers/{provider_id}", handle_providers_delete)
    app.router.add_post("/api/providers/{provider_id}/test", handle_providers_test)
    app.router.add_post("/api/providers/{provider_id}/models", handle_providers_models)
    app.router.add_post("/api/service/restart", handle_service_restart)
    app.router.add_post("/api/config/test-embedding", handle_test_embedding)
    app.router.add_post("/api/documents/rebuild-index", handle_rebuild_index_pending)
    app.router.add_get("/api/documents/rebuild-index/active", handle_milvus_build_active)
    app.router.add_get("/api/documents/ingest/active", handle_ingest_active)
    app.router.add_get("/api/documents/pending-reindex-count", handle_pending_reindex_count)
    # 预留端口（reserved，未实现回 501 + available_in）
    app.router.add_post("/api/sync/{target}", handle_sync)
    app.router.add_post("/api/notion/init", handle_notion_init)
    app.router.add_post("/api/notion/push-note", handle_notion_push_note)
    app.router.add_get("/api/sync/notion/status", handle_notion_status)
    app.router.add_get("/api/sync/status", handle_sync_status)
    app.router.add_get("/api/sync/notion/active", handle_notion_active)
    app.router.add_get("/api/zotero/config", handle_zotero_config)
    app.router.add_get("/api/zotero/probe", handle_zotero_probe)
    app.router.add_post("/api/zotero/server-key", handle_zotero_server_key_post)
    app.router.add_delete("/api/zotero/server-key", handle_zotero_server_key_delete)
    app.router.add_post("/api/zotero/account-change", handle_zotero_account_change)
    app.router.add_post("/api/sync/zotero/pull", handle_zotero_pull)
    app.router.add_get("/api/sync/zotero/status", handle_zotero_status)
    app.router.add_get("/api/sync/zotero/active", handle_zotero_active)
    app.router.add_post("/api/backup", handle_backup)
    app.router.add_post("/api/restore", handle_restore)
    app.router.add_get("/api/r2/status", handle_r2_status)
    app.router.add_get("/api/r2/job", handle_r2_job)
    app.router.add_post("/api/graph/build/estimate", handle_graph_build_estimate)
    app.router.add_post("/api/graph/build", handle_graph_build)
    app.router.add_post("/api/graph/codex-build", handle_codex_graph_build)
    app.router.add_get("/api/graph/codex-build/{job_id}/next", handle_codex_graph_next)
    app.router.add_post("/api/graph/codex-build/{job_id}/submit", handle_codex_graph_submit)
    app.router.add_post("/api/graph/codex-build/{job_id}/retry", handle_codex_graph_retry)
    app.router.add_get("/api/graph/build/active", handle_graph_build_active)
    app.router.add_get("/api/graph/build/history", handle_graph_build_history)
    app.router.add_get("/api/graph/build/{job_id}", handle_graph_build_job)
    app.router.add_post("/api/graph/build/{job_id}/pause", handle_graph_build_pause)
    app.router.add_post("/api/graph/build/{job_id}/resume", handle_graph_build_resume)
    app.router.add_post("/api/graph/build/{job_id}/cancel", handle_graph_build_cancel)
    app.router.add_get("/api/graph/stats", handle_graph_stats)
    app.router.add_get("/api/ask/progress/{cid}", handle_ask_progress)
    app.router.add_get("/api/system/info", handle_system_info)
    app.router.add_get("/api/system/models", handle_model_runtime)
    app.router.add_post("/api/system/models/unload", handle_model_unload)
    app.router.add_get("/api/fs/browse", handle_fs_browse)
    app.router.add_get("/api/models/local", handle_list_local_models)
    app.router.add_delete("/api/models/local/{name}", handle_delete_local_model)
    app.router.add_get("/api/capabilities", handle_capabilities)
    app.router.add_get("/api/dependencies", handle_dependencies)
    app.router.add_post("/api/dependencies/install", handle_dependency_install)
    app.router.add_post("/api/dependencies/recheck", handle_dependency_recheck)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_post("/api/logs/events", handle_log_event)
    app.router.add_post("/api/ask/evidence", handle_ask_evidence)
    app.router.add_post("/api/ask", handle_ask)
    app.router.add_get("/api/console/scope-state", handle_console_scope_state_get)
    app.router.add_put("/api/console/scope-state", handle_console_scope_state_upsert)
    app.router.add_patch("/api/console/scope-state", handle_console_scope_state_upsert)
    app.router.add_get("/api/chat/history", handle_chat_history_get)
    app.router.add_delete("/api/chat/history", handle_chat_history_clear)
    app.router.add_patch(
        "/api/chat/history/{conv_id}/messages/{msg_idx}/lock", handle_chat_message_lock
    )

    from web.desktop_routes import register_desktop_routes

    register_desktop_routes(app, _api)

    from web.agent_routes import register_agent_routes

    register_agent_routes(app, _api, agent_access, app_version)

    # 日志 handler 已在路由装配前注入；生产由组合根传入，独立测试/开发则幂等安装默认实例。
    return app


__all__ = ["ACCESS_TOKEN_QUERY", "DEFAULT_ALLOWED_ORIGINS", "build_app"]
