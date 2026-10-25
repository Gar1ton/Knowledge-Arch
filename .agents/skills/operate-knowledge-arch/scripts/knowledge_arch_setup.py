"""First-run setup commands for the Knowledge Arch skill client (standard library only).

The functions take a client object exposing ``request_json(path, method=..., body=...,
query_params=...)``. Usage problems raise ``ValueError``; the client turns them into its own
usage error. Every mutation previews by default and only writes with ``apply=True``. Secrets are
never accepted here: ``request_secret`` asks the user to type them into Knowledge Arch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import urlopen


class Client(Protocol):
    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        query_params: dict[str, object] | None = None,
        body: Any = None,
    ) -> Any: ...


CORE_STAGES = ("models", "embedding", "vector_store", "retrieval", "ask", "ingest")
OPTIONAL_STAGES = ("zotero", "graph", "notion", "r2")
MODEL_ROLES = ("flow.models.chat", "flow.models.research", "flow.models.graph")
SECRET_WAIT_POLL_SECONDS = 2.0
SECRET_WAIT_MAX_SECONDS = 900
LOCAL_SERVICES = {
    "ollama": "http://127.0.0.1:11434/v1/models",
    "lmstudio": "http://127.0.0.1:1234/v1/models",
}


def _dict(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Knowledge Arch returned an unexpected payload for {endpoint}")
    return value


def _schema(client: Client, home: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, object] | None = {"home": home} if home else None
    data = _dict(client.request_json("/api/settings/schema", query_params=params), "schema")
    return [item for item in data.get("items", []) if isinstance(item, dict)]


def _provider_summary(provider: dict[str, Any]) -> dict[str, Any]:
    status = provider.get("status") if isinstance(provider.get("status"), dict) else {}
    return {
        "id": provider.get("id"),
        "label": provider.get("label"),
        "kind": provider.get("kind"),
        "base_url": provider.get("base_url"),
        "api_key_set": bool(provider.get("api_key_set")),
        "last_test": status.get("status") if status else None,
        "used_by": provider.get("referenced_by") or [],
    }


# ── 状态与探测 ────────────────────────────────────────────────────────


def setup_status(client: Client) -> dict[str, Any]:
    """One compact view of what is configured, what is missing and what waits on the user."""
    caps = _dict(client.request_json("/api/capabilities"), "/api/capabilities")
    stages = {
        str(stage.get("id")): stage for stage in caps.get("pipeline", []) if isinstance(stage, dict)
    }
    providers = _dict(client.request_json("/api/providers"), "/api/providers")
    values = {item["key"]: item.get("value") for item in _schema(client, "flow")}
    secrets = _dict(client.request_json("/api/agent/secret-requests"), "secret-requests")

    def stage_view(stage_id: str) -> dict[str, Any]:
        stage = stages.get(stage_id, {})
        return {
            "id": stage_id,
            "status": stage.get("status", "unknown"),
            "configured": stage.get("configured"),
            "current": stage.get("current"),
        }

    core = [stage_view(s) for s in CORE_STAGES]
    missing = [s["id"] for s in core if s["status"] != "ready" or s["configured"] is False]
    return {
        "status": "ready" if not missing else "incomplete",
        "core_stages": core,
        "optional_stages": [stage_view(s) for s in OPTIONAL_STAGES],
        "missing_core": missing,
        "providers": [_provider_summary(p) for p in providers.get("providers", [])],
        "models": {key: values.get(key) for key in MODEL_ROLES},
        "embedding_source": values.get("flow.embedding.source"),
        "rerank_provider": values.get("flow.retrieval.rerank.provider"),
        "rerank_remote": values.get("flow.retrieval.rerank.remote"),
        "pending_secret_requests": [
            {"id": r.get("id"), "label": r.get("label")}
            for r in secrets.get("requests", [])
            if isinstance(r, dict) and r.get("status") == "pending"
        ],
    }


def _zotero_candidates() -> list[Path]:
    home = Path.home()
    candidates = [home / "Zotero"]
    if os.name == "nt":
        candidates.append(Path(os.environ.get("USERPROFILE", home)) / "Zotero")
    return candidates


def _probe(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed 127.0.0.1 URLs only
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, ValueError):
        return {"running": False}
    models = payload.get("data") if isinstance(payload, dict) else None
    names = [m.get("id") for m in models or [] if isinstance(m, dict)]
    return {"running": True, "models": names[:30]}


def detect(client: Client) -> dict[str, Any]:
    """Probe this computer: Zotero data directory and local model servers (nothing else)."""
    del client  # 只探测本机；不向 Knowledge Arch 要运行环境（其中是带用户名的路径）
    zotero = [str(p) for p in _zotero_candidates() if (p / "zotero.sqlite").is_file()]
    return {
        "zotero_data_dirs": sorted(set(zotero)),
        "local_model_servers": {name: _probe(url) for name, url in LOCAL_SERVICES.items()},
        "platform": sys.platform,
    }


# ── 连接 ──────────────────────────────────────────────────────────────


def providers_list(client: Client) -> dict[str, Any]:
    data = _dict(client.request_json("/api/providers"), "/api/providers")
    return {
        "providers": [_provider_summary(p) for p in data.get("providers", [])],
        "presets": data.get("presets", []),
    }


def provider_add(
    client: Client,
    *,
    preset: str | None,
    label: str | None,
    base_url: str | None,
    kind: str | None,
    provider_id: str | None,
    apply: bool,
) -> dict[str, Any]:
    """Create a connection without its key; the key always comes from request-secret."""
    data = _dict(client.request_json("/api/providers"), "/api/providers")
    body: dict[str, Any] = {}
    needs_key = True
    if preset:
        match = next((p for p in data.get("presets", []) if p.get("id") == preset), None)
        if match is None:
            known = ", ".join(str(p.get("id")) for p in data.get("presets", []))
            raise ValueError(f"Unknown preset {preset}; choose one of: {known}")
        body = {"label": match["label"], "base_url": match["base_url"], "kind": match["kind"]}
        needs_key = bool(match.get("needs_key", True))
    for field, value in (("label", label), ("base_url", base_url), ("kind", kind),
                         ("id", provider_id)):
        if value:
            body[field] = value
    if not body.get("label") or not body.get("base_url"):
        raise ValueError("provider-add needs --preset, or both --label and --base-url")
    if not apply:
        return {"status": "preview", "mutation_performed": False, "would_create": body,
                "needs_key": needs_key, "next": "rerun with --apply after the user confirms"}
    created = _dict(client.request_json("/api/providers", method="POST", body=body), "create")
    return {"status": "applied", "mutation_performed": True,
            "provider": _provider_summary(created), "needs_key": needs_key}


def provider_test(client: Client, provider_id: str) -> dict[str, Any]:
    from urllib.parse import quote

    path = f"/api/providers/{quote(provider_id, safe='')}/test"
    return _dict(client.request_json(path, method="POST", body={}), path)


def provider_models(client: Client, provider_id: str) -> dict[str, Any]:
    from urllib.parse import quote

    path = f"/api/providers/{quote(provider_id, safe='')}/models"
    result = _dict(client.request_json(path, method="POST", body={}), path)
    models = result.get("models") or []
    return {**result, "models": models[:200], "model_count": len(models)}


# ── 设置 ──────────────────────────────────────────────────────────────


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    view = {
        "key": item.get("key"),
        "kind": item.get("kind"),
        "value": item.get("value"),
        "is_set": item.get("is_set"),
        "consequence": item.get("consequence"),
    }
    if item.get("options"):
        view["options"] = item["options"]
    if item.get("bounds"):
        view["bounds"] = item["bounds"]
    return view


def settings_show(client: Client, home: str | None) -> dict[str, Any]:
    items = _schema(client, home)
    return {"home": home or "", "items": [_compact_item(i) for i in items]}


def settings_set(client: Client, key: str, value: Any, *, apply: bool) -> dict[str, Any]:
    """Preview, then write one non-secret setting. Model references are {provider_id, model}."""
    item = next((i for i in _schema(client) if i.get("key") == key), None)
    if item is None:
        raise ValueError(f"Unknown setting: {key}")
    if item.get("kind") == "secret":
        raise ValueError(f"{key} is a secret; use request-secret --target setting:{key}")
    if item.get("kind") == "path" and isinstance(value, str):
        value = os.path.expanduser(value)  # 输出里的家目录显示为 ~，写回前展开
    preview = {
        "key": key,
        "from": item.get("value"),
        "to": value,
        "consequence": item.get("consequence"),
    }
    if not apply:
        return {"status": "preview", "mutation_performed": False, **preview,
                "next": "rerun with --apply after the user confirms"}
    result = client.request_json("/api/settings", method="PATCH", body={"key": key, "value": value})
    return {"status": "applied", "mutation_performed": True, **preview, "api_result": result}


def component_install(client: Client, key: str, *, apply: bool) -> dict[str, Any]:
    from urllib.parse import quote

    data = _dict(client.request_json("/api/components"), "/api/components")
    component = next((c for c in data.get("components", []) if c.get("key") == key), None)
    if component is None:
        known = ", ".join(str(c.get("key")) for c in data.get("components", []))
        raise ValueError(f"Unknown component {key}; choose one of: {known}")
    if not apply:
        return {"status": "preview", "mutation_performed": False, "component": component,
                "warning": "downloads packages into the Knowledge Arch data directory; "
                "restart the service afterwards",
                "next": "rerun with --apply after the user confirms"}
    path = f"/api/components/{quote(key, safe='')}/install"
    result = client.request_json(path, method="POST", body={})
    return {"status": "applied", "mutation_performed": True, "api_result": result}


# ── 密钥请求 ──────────────────────────────────────────────────────────


def request_secret(client: Client, target: str, reason: str) -> dict[str, Any]:
    item = _dict(
        client.request_json(
            "/api/agent/secret-requests", method="POST", body={"target": target, "reason": reason}
        ),
        "secret-requests",
    )
    return {
        "status": item.get("status"),
        "request_id": item.get("id"),
        "label": item.get("label"),
        "next": "Tell the user Knowledge Arch is showing a field for this key, then run "
        f"secret-status --request-id {item.get('id')} --wait",
    }


def secret_status(
    client: Client, request_id: str, *, wait: bool, max_wait_seconds: int = SECRET_WAIT_MAX_SECONDS
) -> dict[str, Any]:
    from urllib.parse import quote

    path = f"/api/agent/secret-requests/{quote(request_id, safe='')}"
    deadline = time.monotonic() + max(0, min(max_wait_seconds, SECRET_WAIT_MAX_SECONDS))
    while True:
        item = _dict(client.request_json(path), path)
        if not wait or item.get("status") != "pending" or time.monotonic() >= deadline:
            return {"request_id": request_id, "status": item.get("status"),
                    "label": item.get("label")}
        time.sleep(SECRET_WAIT_POLL_SECONDS)


# ── 命令行 ────────────────────────────────────────────────────────────

SETUP_COMMANDS = (
    "setup-status", "detect", "providers", "provider-add", "provider-test", "provider-models",
    "settings-show", "settings-set", "component-install", "request-secret", "secret-status",
)


def add_setup_parsers(subparsers: Any) -> None:
    subparsers.add_parser("setup-status", help="Show configured, missing and pending items")
    subparsers.add_parser("detect", help="Detect Zotero and local model servers on this computer")
    subparsers.add_parser("providers", help="List model connections and presets")
    add = subparsers.add_parser("provider-add", help="Preview or create a connection (no key)")
    add.add_argument("--preset")
    add.add_argument("--label")
    add.add_argument("--base-url")
    add.add_argument("--kind", choices=("openai_compatible", "anthropic"))
    add.add_argument("--id", dest="provider_id")
    add.add_argument("--apply", action="store_true")
    for name, help_text in (("provider-test", "Test a connection"),
                            ("provider-models", "Fetch a connection's model list")):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--id", dest="provider_id", required=True)
    show = subparsers.add_parser("settings-show", help="Show settings of one node or all")
    show.add_argument("--home", help="e.g. flow.models, flow.embedding, prefs.general")
    set_parser = subparsers.add_parser("settings-set", help="Preview or write one setting")
    set_parser.add_argument("key")
    set_parser.add_argument("value", help="JSON literal, e.g. true, 8, \"en\", or "
                            '{"provider_id":"ds","model":"deepseek-chat"}')
    set_parser.add_argument("--apply", action="store_true")
    comp = subparsers.add_parser("component-install", help="Preview or install a component")
    comp.add_argument("key")
    comp.add_argument("--apply", action="store_true")
    secret = subparsers.add_parser("request-secret", help="Ask the user to type a key into the app")
    secret.add_argument("--target", required=True,
                        help='"provider:<connection id>" or "setting:<secret setting key>"')
    secret.add_argument("--reason", default="")
    wait = subparsers.add_parser("secret-status", help="Check or wait for a secret request")
    wait.add_argument("--request-id", required=True)
    wait.add_argument("--wait", action="store_true")
    wait.add_argument("--max-wait-seconds", type=int, default=SECRET_WAIT_MAX_SECONDS)


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def run_setup_command(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command == "setup-status":
        return setup_status(client)
    if command == "detect":
        return detect(client)
    if command == "providers":
        return providers_list(client)
    if command == "provider-add":
        return provider_add(client, preset=args.preset, label=args.label, base_url=args.base_url,
                            kind=args.kind, provider_id=args.provider_id, apply=args.apply)
    if command == "provider-test":
        return provider_test(client, args.provider_id)
    if command == "provider-models":
        return provider_models(client, args.provider_id)
    if command == "settings-show":
        return settings_show(client, args.home)
    if command == "settings-set":
        return settings_set(client, args.key, _parse_value(args.value), apply=args.apply)
    if command == "component-install":
        return component_install(client, args.key, apply=args.apply)
    if command == "request-secret":
        return request_secret(client, args.target, args.reason)
    if command == "secret-status":
        return secret_status(client, args.request_id, wait=args.wait,
                             max_wait_seconds=args.max_wait_seconds)
    raise ValueError(f"Unknown setup command: {command}")


__all__ = ["SETUP_COMMANDS", "add_setup_parsers", "run_setup_command"]
