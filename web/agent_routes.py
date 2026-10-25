"""AI 助手接入的 HTTP 路由与权限（见 ka_server/agent_access.py、kacore/agent_skill.py）。

- **白名单**：agent token 只能访问 `AGENT_ROUTES` 里的 (方法, 路由模板)；关机、恢复、删除、文件浏览
  与本模块里只给界面用的接口（skill 安装、填写密钥）都不在内。
- **机密**：agent 不能写任何机密（provider 的 api_key、kind=secret 的设置、Zotero key）。需要密钥时
  它创建「密钥请求」，界面弹框让用户直接填进应用；agent 只能看到请求状态，永远拿不到值。
- 路由只做 HTTP 翻译；接入状态对象由 ka_server 注入，未注入时（测试 / 开发）agent token 一律不认。
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from aiohttp import web

from kacore import agent_skill
from kacore.settings.schema import SPECS_BY_KEY

if TYPE_CHECKING:
    from pathlib import Path

    from kacore.api import KnowledgeRepositoryApi

ApiOf = Callable[[web.Request], "KnowledgeRepositoryApi"]

AGENT_ACCESS_SETTING = "prefs.general.agent_access"
_SECRET_FIELDS = ("api_key", "key")
_MAX_PENDING_SECRET_REQUESTS = 20


class AgentAccessLike(Protocol):
    enabled: bool
    connection_file: Path

    def accepts(self, token: str) -> bool: ...

    def touch(self) -> None: ...

    def set_enabled(self, enabled: bool) -> None: ...

    def status(self) -> dict[str, object]: ...


AGENT_ACCESS_KEY = web.AppKey("agent_access", object)
AGENT_REQUEST_FLAG = web.RequestKey("ka_agent", bool)
_SECRET_REQUESTS_KEY = web.AppKey("agent_secret_requests", object)
_VERSION_KEY = web.AppKey("agent_app_version", str)

AGENT_ROUTES: frozenset[tuple[str, str]] = frozenset({
    # 状态与配置
    ("GET", "/api/health"),
    ("GET", "/api/capabilities"),
    ("GET", "/api/settings/schema"),
    ("PATCH", "/api/settings"),
    ("GET", "/api/config/effective"),
    ("POST", "/api/config/update"),
    ("POST", "/api/config/test-embedding"),
    ("POST", "/api/plugin/restart"),
    ("GET", "/api/providers"),
    ("POST", "/api/providers"),
    ("PATCH", "/api/providers/{provider_id}"),
    ("POST", "/api/providers/{provider_id}/test"),
    ("POST", "/api/providers/{provider_id}/models"),
    ("GET", "/api/components"),
    ("GET", "/api/dependencies"),
    ("POST", "/api/components/{key}/install"),
    ("GET", "/api/models/local"),
    ("GET", "/api/zotero/config"),
    ("GET", "/api/zotero/probe"),
    ("GET", "/api/sync/status"),
    ("GET", "/api/sync/zotero/status"),
    ("POST", "/api/sync/zotero/pull"),
    ("GET", "/api/sync/notion/status"),
    # 检索、阅读与问答记录
    ("GET", "/api/collections"),
    ("GET", "/api/documents"),
    ("GET", "/api/documents/{doc_id}/content/page"),
    ("GET", "/api/documents/{doc_id}/chunks"),
    ("GET", "/api/kb/collections"),
    ("GET", "/api/kb/search"),
    ("GET", "/api/kb/chunk-context"),
    ("POST", "/api/ask/evidence"),
    ("POST", "/api/notion/push-note"),
    # 索引与知识图谱
    ("GET", "/api/documents/pending-reindex-count"),
    ("GET", "/api/documents/rebuild-index/active"),
    ("POST", "/api/documents/rebuild-index"),
    ("GET", "/api/graph/stats"),
    ("GET", "/api/graph/build/active"),
    ("GET", "/api/graph/build/{job_id}"),
    ("POST", "/api/graph/build/estimate"),
    ("POST", "/api/graph/codex-build"),
    ("GET", "/api/graph/codex-build/{job_id}/next"),
    ("POST", "/api/graph/codex-build/{job_id}/submit"),
    ("POST", "/api/graph/codex-build/{job_id}/retry"),
    # 本模块
    ("GET", "/api/agent/status"),
    ("GET", "/api/agent/secret-requests"),
    ("POST", "/api/agent/secret-requests"),
    ("GET", "/api/agent/secret-requests/{request_id}"),
    ("POST", "/api/agent/secret-requests/{request_id}/cancel"),
})


def _canonical(request: web.Request) -> str:
    try:
        return str(request.match_info.route.resource.canonical)  # type: ignore[union-attr]
    except (AttributeError, RuntimeError):
        return request.path


def agent_access(app: web.Application) -> AgentAccessLike | None:
    return app.get(AGENT_ACCESS_KEY)  # type: ignore[return-value]


def is_agent(request: web.Request) -> bool:
    return bool(request.get(AGENT_REQUEST_FLAG))


def authorize_agent(request: web.Request, token: str) -> web.Response | None:
    """token 是 agent token 时：记下来访、按白名单放行。

    返回 None 表示放行、Response 表示拒绝；不是 agent token 时抛 KeyError（调用方回 401）。
    """
    access = agent_access(request.app)
    if access is None or not access.accepts(token):
        raise KeyError("not an agent token")
    access.touch()
    request[AGENT_REQUEST_FLAG] = True
    if (request.method, _canonical(request)) not in AGENT_ROUTES:
        return web.json_response(
            {"status": "error", "message": "AI assistants cannot use this endpoint"}, status=403
        )
    return None


def _secret_denied() -> web.Response:
    return web.json_response(
        {
            "status": "error",
            "code": "secret_requires_user",
            "message": "AI assistants cannot write secrets. Create a secret request "
            "(request-secret) so the user can type it into Knowledge Arch.",
        },
        status=403,
    )


def guard_secret_setting(request: web.Request, key: object) -> web.Response | None:
    """PATCH /api/settings：agent 写 kind=secret 的设置 → 403。"""
    spec = SPECS_BY_KEY.get(str(key))
    if is_agent(request) and spec is not None and spec.kind == "secret":
        return _secret_denied()
    return None


def guard_secret_fields(request: web.Request, body: dict[str, Any]) -> web.Response | None:
    """providers 的创建 / 修改：agent 请求体里带密钥字段 → 403。"""
    if is_agent(request) and any(body.get(field) for field in _SECRET_FIELDS):
        return _secret_denied()
    return None


def after_setting_change(request: web.Request, key: str, value: Any) -> None:
    """设置写入成功后的进程级副作用：agent 接入开关即时生效。"""
    access = agent_access(request.app)
    if key == AGENT_ACCESS_SETTING and access is not None:
        access.set_enabled(bool(value))


# ── 密钥请求 ──────────────────────────────────────────────────────────


class SecretRequests:
    """进程内的密钥请求表；只存目标与状态，用户填写的值直接写进加密库、不在这里停留。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def create(self, target: str, label: str, reason: str) -> dict[str, Any]:
        pending = [i for i in self._items.values() if i["status"] == "pending"]
        for item in pending:
            if item["target"] == target:
                return item  # 同一目标只保留一个待填请求
        if len(pending) >= _MAX_PENDING_SECRET_REQUESTS:
            raise ValueError("too many pending secret requests")
        item = {
            "id": secrets.token_hex(6),
            "target": target,
            "label": label,
            "reason": reason[:300],
            "status": "pending",
            "created_at": time.time(),
        }
        self._items[item["id"]] = item
        return item

    def get(self, request_id: str) -> dict[str, Any]:
        return self._items[request_id]

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._items.values(), key=lambda item: item["created_at"])

    def finish(self, request_id: str, status: str) -> dict[str, Any]:
        item = self._items[request_id]
        item["status"] = status
        item["finished_at"] = time.time()
        return item


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"status": "error", "message": message}, status=status)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register_agent_routes(
    app: web.Application, api_of: ApiOf, access: AgentAccessLike | None, app_version: str
) -> None:
    app[AGENT_ACCESS_KEY] = access
    app[_SECRET_REQUESTS_KEY] = SecretRequests()
    app[_VERSION_KEY] = app_version

    def store(request: web.Request) -> SecretRequests:
        return request.app[_SECRET_REQUESTS_KEY]  # type: ignore[return-value]

    def connection_file() -> Path | None:
        return access.connection_file if access is not None else None

    async def status(request: web.Request) -> web.Response:
        """GET /api/agent/status — 开关、是否已连上、待填密钥数、skill 安装情况。"""
        data: dict[str, Any] = (
            access.status() if access is not None else {"enabled": False, "connected": False}
        )
        data["pending_secret_requests"] = sum(
            1 for item in store(request).list() if item["status"] == "pending"
        )
        if not is_agent(request):
            data["skill"] = agent_skill.install_status(app_version)
        return web.json_response(data)

    async def prompt(request: web.Request) -> web.Response:
        """GET /api/agent/prompt?lang=zh|en — 首次配置 prompt（含本机绝对路径）。"""
        path = connection_file()
        if path is None:
            return _error("AI assistant access is not available in this build", 501)
        try:
            skill_dir = agent_skill.skill_source_dir()
        except FileNotFoundError as exc:
            return _error(str(exc), 501)
        lang = request.query.get("lang", "en")
        return web.json_response({
            "prompt": agent_skill.build_prompt(lang, skill_dir=skill_dir, connection_file=path),
            "command": agent_skill.client_command(skill_dir, path),
            "skill_dir": str(skill_dir),
        })

    async def install_skill(request: web.Request) -> web.Response:
        """POST /api/agent/skill/install {targets: ["codex", "claude"]} — 只给界面用。"""
        path = connection_file()
        if path is None:
            return _error("AI assistant access is not available in this build", 501)
        body = await _json_body(request)
        targets = [t for t in body.get("targets", agent_skill.TARGETS) if t in agent_skill.TARGETS]
        if not targets:
            return _error("targets must name codex and/or claude", 400)
        try:
            result = agent_skill.install_skill(targets, version=app_version, connection_file=path)
        except (OSError, FileNotFoundError) as exc:
            return _error(f"Could not install the skill: {exc}", 500)
        return web.json_response({"status": "ok", "targets": result})

    async def secret_list(request: web.Request) -> web.Response:
        return web.json_response({"requests": store(request).list()})

    async def secret_create(request: web.Request) -> web.Response:
        """POST /api/agent/secret-requests {target: "provider:<id>" | "setting:<key>", reason}。"""
        body = await _json_body(request)
        target = str(body.get("target") or "")
        kind, _, ref = target.partition(":")
        if kind == "provider":
            providers = api_of(request).list_providers()["providers"]
            match = next((p for p in providers if p.get("id") == ref), None)
            if match is None:
                return _error(f"Connection {ref} does not exist", 404)
            label = f"{match.get('label') or ref} API key"
        elif kind == "setting":
            spec = SPECS_BY_KEY.get(ref)
            if spec is None or spec.kind != "secret":
                return _error(f"{ref} is not a secret setting", 400)
            label = ref
        else:
            return _error('target must be "provider:<id>" or "setting:<key>"', 400)
        try:
            item = store(request).create(target, label, str(body.get("reason") or ""))
        except ValueError as exc:
            return _error(str(exc), 429)
        return web.json_response(item, status=201)

    async def secret_get(request: web.Request) -> web.Response:
        try:
            return web.json_response(store(request).get(request.match_info["request_id"]))
        except KeyError:
            return _error("secret request not found", 404)

    async def secret_fulfill(request: web.Request) -> web.Response:
        """POST /api/agent/secret-requests/{id}/fulfill {value} — 只给界面用。

        值直接写进加密库，本表只记状态。
        """
        try:
            item = store(request).get(request.match_info["request_id"])
        except KeyError:
            return _error("secret request not found", 404)
        if item["status"] != "pending":
            return _error("this secret request is no longer pending", 409)
        value = str((await _json_body(request)).get("value") or "").strip()
        if not value:
            return _error("value is required", 400)
        kind, _, ref = str(item["target"]).partition(":")
        api = api_of(request)
        try:
            if kind == "provider":
                await api.update_provider(ref, {"api_key": value})
            else:
                await api.update_setting(ref, value)
        except (KeyError, ValueError, RuntimeError) as exc:
            return _error(str(exc), 400)
        return web.json_response(store(request).finish(item["id"], "fulfilled"))

    async def secret_cancel(request: web.Request) -> web.Response:
        try:
            item = store(request).get(request.match_info["request_id"])
        except KeyError:
            return _error("secret request not found", 404)
        if item["status"] != "pending":
            return web.json_response(item)
        return web.json_response(store(request).finish(item["id"], "cancelled"))

    app.router.add_get("/api/agent/status", status)
    app.router.add_get("/api/agent/prompt", prompt)
    app.router.add_post("/api/agent/skill/install", install_skill)
    app.router.add_get("/api/agent/secret-requests", secret_list)
    app.router.add_post("/api/agent/secret-requests", secret_create)
    app.router.add_get("/api/agent/secret-requests/{request_id}", secret_get)
    app.router.add_post("/api/agent/secret-requests/{request_id}/fulfill", secret_fulfill)
    app.router.add_post("/api/agent/secret-requests/{request_id}/cancel", secret_cancel)


__all__ = [
    "AGENT_ACCESS_SETTING",
    "AGENT_ROUTES",
    "AgentAccessLike",
    "after_setting_change",
    "authorize_agent",
    "guard_secret_fields",
    "guard_secret_setting",
    "is_agent",
    "register_agent_routes",
]
