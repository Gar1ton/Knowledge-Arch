#!/usr/bin/env python3
"""Token-efficient, standard-library client for the local Knowledge Arch desktop app API.

Knowledge Arch writes its address and a per-launch agent token to a connection file while
"Allow AI assistants to connect" is on. This client reads that file on every call, so an app
restart (which rotates the token) needs no re-registration. Run it with the Python that ships
with Knowledge Arch; nothing has to be installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

# `python -I` leaves the script directory off sys.path; the setup commands live next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from knowledge_arch_setup import SETUP_COMMANDS, add_setup_parsers, run_setup_command  # noqa: E402

CONNECTION_VERSION = 1
CONNECTION_FILE_NAME = "agent-connection.json"
ENV_CONNECTION_FILE = "KNOWLEDGE_ARCH_CONNECTION_FILE"
ENV_URL = "KNOWLEDGE_ARCH_URL"
ENV_TOKEN = "KNOWLEDGE_ARCH_TOKEN"


DEFAULT_TIMEOUT_SECONDS = 30
# index-rebuild --wait 的轮询节奏与上限。重建是后台 job，服务端 POST 立即返回；
# 上限到点只是停止观察，**不会**取消仍在跑的重建（用 index-status 继续查）。
INDEX_REBUILD_POLL_SECONDS = 3.0
INDEX_REBUILD_WAIT_MAX_SECONDS = 1800
# 终态集合，与 kacore/milvus_build.py 的 MILVUS_BUILD_TERMINAL_STATUSES 对齐。
INDEX_REBUILD_TERMINAL_STATUSES = ("success", "partial_failure", "error")
RRF_K = 60
MAX_QUERIES = 4
MAX_FINAL_HITS = 20
MAX_HIT_CHARS = 4000
MAX_NOTION_QA_ITEMS = 20
MAX_NOTION_QA_QUESTION_CHARS = 2000
MAX_NOTION_QA_ANSWER_CHARS = 100000
ASK_EVIDENCE_MODES = ("default", "enhanced", "deep_thinking")
READ_INTENTS = ("anchored", "full-text")

# Keep this compact distribution-side snapshot synchronized with kacore.settings.schema.
CONFIG_POLICIES: dict[str, dict[str, str]] = {
    "ask": {
        "answer_language": "none",
        "llm_timeout_seconds": "restart",
        "system_prompt": "none",
        "task_timeout_seconds": "none",
    },
    "deep_thinking": {
        "max_rounds": "none",
        "max_sub_queries": "none",
        "max_verify_rounds": "none",
        "rerank_weight": "none",
        "verify_enabled": "none",
        "wide_top_k": "none",
    },
    "embedding": {
        "cache_mb": "restart",
        "device": "restart",
        "load_timeout_seconds": "restart",
        "local_idle_timeout_seconds": "restart",
        "max_token_size": "rebuild",
        "model": "rebuild",
        "provider": "rebuild",
    },
    "enhanced_recall": {
        "corrective_enabled": "none",
        "max_final_evidence": "none",
        "max_sub_queries": "none",
        "rerank_weight": "none",
        "wide_top_k": "none",
    },
    "graph": {
        "embedding_max_async": "restart",
        "enabled": "restart",
        "lightrag_llm_timeout_seconds": "restart",
        "llm_max_async": "restart",
        "max_doc_chars": "rebuild",
        "query_mode": "restart",
    },
    "notion_sync": {
        "auto_sync_enabled": "none",
        "auto_sync_interval_sec": "none",
        "data_source_id": "none",
        "database_id": "none",
        "database_title": "none",
        "enabled": "restart",
        "parent_page_id": "none",
        "qa_data_source_id": "none",
        "qa_database_id": "none",
        "sync_mode": "none",
    },
    "r2_sync": {
        "account_id": "restart",
        "backup_interval_sec": "restart",
        "bucket": "restart",
        "enabled": "restart",
        "warn_threshold": "restart",
    },
    "rerank": {
        "batch_size": "restart",
        "device": "restart",
        "keep": "restart",
        "max_candidates": "restart",
        "model": "none",
        "provider": "none",
    },
    "source_store": {
        "default_collection": "restart",
        "ocr_enabled": "restart",
    },
    "vector_db": {
        "auto_index_enabled": "restart",
        "auto_rebuild_delay_seconds": "none",
        "auto_rebuild_enabled": "none",
        "backend": "restart",
    },
    "zotero_sync": {
        "access_mode": "restart",
        "api_port": "restart",
        "auto_sync_enabled": "restart",
        "auto_sync_interval_sec": "restart",
        "enabled": "restart",
        "linked_root": "rebuild",
        "storage_mode": "rebuild",
        "sync_mode": "rebuild",
        "zotero_data_dir": "restart",
        "zotmoov_root": "rebuild",
    },
}

_SECRET_MARKERS = ("password", "secret", "api_key", "access_key", "token")


class ClientError(RuntimeError):
    """Expected, user-actionable client failure."""

    code = "client_error"
    exit_code = 2


class AuthenticationError(ClientError):
    code = "authentication_error"
    exit_code = 3


class ConnectionFailure(ClientError):
    code = "connection_error"
    exit_code = 4


class ApiError(ClientError):
    code = "api_error"
    exit_code = 5


class StructuredApiError(ApiError):
    """An API error whose JSON body carries a machine-readable ``status`` discriminator.

    Subclasses ApiError on purpose: every existing ``except ApiError`` site and the
    ``exit_code`` contract keep working untouched. Callers that care about a specific
    gate (e.g. the full-text confirmation gate) can inspect ``http_status``/``payload``.
    """

    def __init__(self, message: str, http_status: int, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.payload = payload


class UsageError(ClientError):
    code = "usage_error"
    exit_code = 2


def _is_secret_key(key: object) -> bool:
    normalized = str(key).lower()
    return any(marker in normalized for marker in _SECRET_MARKERS)


def redact_secrets(value: Any) -> Any:
    """Recursively replace secret-shaped values without echoing masked API payloads."""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _is_secret_key(key) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def redact_home(text: str) -> str:
    """Show the home directory as ~ so the user name never reaches the assistant's provider.

    Matches whole path prefixes only (followed by a separator, quote, space or the end), including
    the JSON-escaped Windows form.
    """
    home = str(Path.home())
    if len(home) <= 1:
        return text
    variants = {home, home.replace("\\", "\\\\"), home.replace("\\", "/")}
    for variant in sorted(variants, key=len, reverse=True):
        text = re.sub(re.escape(variant) + r'(?=[/\\"\s]|$)', "~", text)
    return text


def parse_value(raw: str) -> Any:
    """Parse JSON literals while keeping ordinary unquoted strings ergonomic."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _bounded(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


class ConnectionNotConfigured(ClientError):
    code = "connection_not_configured"
    exit_code = 6


_NOT_CONNECTED = (
    "Knowledge Arch is not reachable: start the app and turn on Preferences > General > "
    "Allow AI assistants to connect (the welcome page does this when you copy the setup prompt)."
)


@dataclass(frozen=True)
class Connection:
    """Address and per-launch agent token published by the running app."""

    base_url: str
    token: str
    pid: int | None


def _normalize_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UsageError("Knowledge Arch URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise UsageError("Do not embed credentials in the Knowledge Arch URL")
    return base_url.strip().rstrip("/")


def default_connection_file() -> Path:
    """Mirror platformdirs.user_config_dir("Knowledge Arch") used by the app."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "Knowledge Arch" / CONNECTION_FILE_NAME


def connection_file_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get(ENV_CONNECTION_FILE)
    return Path(override) if override else default_connection_file()


def _pid_alive(pid: int | None) -> bool:
    if not pid or os.name == "nt":
        return True  # Windows: os.kill(pid, 0) would terminate; rely on the HTTP probe instead
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_connection(path: Path | None = None) -> Connection:
    target = connection_file_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConnectionNotConfigured(_NOT_CONNECTED) from None
    except (OSError, ValueError) as exc:
        raise ConnectionNotConfigured(f"Cannot read the connection file: {exc}") from None
    if not isinstance(raw, dict) or raw.get("version") != CONNECTION_VERSION:
        raise ConnectionNotConfigured("Unsupported connection file; update Knowledge Arch.")
    try:
        connection = Connection(
            base_url=_normalize_base_url(str(raw["url"])),
            token=str(raw["token"]),
            pid=int(raw["pid"]) if raw.get("pid") else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectionNotConfigured("The connection file is incomplete.") from exc
    if not connection.token:
        raise ConnectionNotConfigured("The connection file has no token.")
    if not _pid_alive(connection.pid):
        raise ConnectionNotConfigured(_NOT_CONNECTED)
    return connection


def connection_status(path: Path | None = None) -> dict[str, str]:
    """Report where the connection comes from, never the address or token."""
    if os.environ.get(ENV_URL) or os.environ.get(ENV_TOKEN):
        if os.environ.get(ENV_URL) and os.environ.get(ENV_TOKEN):
            return {"status": "environment_override"}
        return {"status": "unconfigured", "reason": "temporary_connection_incomplete"}
    try:
        load_connection(path)
    except ConnectionNotConfigured as exc:
        return {"status": "not_running", "message": str(exc)}
    return {"status": "connected"}


def resolve_connection_client(path: Path | None = None) -> tuple[KnowledgeArchClient, str]:
    """Environment override (development) first, then the app's connection file."""
    env_url = os.environ.get(ENV_URL)
    env_token = os.environ.get(ENV_TOKEN)
    if env_url or env_token:
        if not env_url or not env_token:
            raise UsageError(f"A temporary connection needs both {ENV_URL} and {ENV_TOKEN}.")
        return KnowledgeArchClient(env_url, env_token), "environment_override"
    connection = load_connection(path)
    return KnowledgeArchClient(connection.base_url, connection.token), "connected"


class KnowledgeArchClient:
    """Synchronous bearer-token client; one short-lived instance per CLI invocation."""

    def __init__(self, base_url: str, token: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = _normalize_base_url(base_url)
        self._token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls, path: Path | None = None) -> KnowledgeArchClient:
        client, _source = resolve_connection_client(path)
        return client

    def _protect_message(self, message: str) -> str:
        return message.replace(self._token, "[redacted]") if self._token else message

    def _request_bytes(
        self,
        path: str,
        *,
        method: str = "GET",
        query_params: dict[str, object] | None = None,
        body: Any = None,
    ) -> bytes:
        url = f"{self.base_url}{path}"
        if query_params:
            url = f"{url}?{urlencode(query_params)}"
        data = None if body is None else _json_bytes(body)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return bytes(response.read())
        except HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            structured: dict[str, Any] | None = None
            try:
                parsed = json.loads(payload)
                detail = parsed.get("message") or parsed.get("error") or payload
                if isinstance(parsed, dict):
                    structured = parsed
            except (json.JSONDecodeError, AttributeError):
                detail = payload or exc.reason
            message = self._protect_message(str(detail))
            if exc.code == 401:
                raise AuthenticationError(
                    "Knowledge Arch rejected the token. It changes whenever the app restarts or "
                    "AI assistant access is turned off; check that access is on and rerun."
                ) from None
            summary = f"Knowledge Arch API returned HTTP {exc.code}: {message}"
            if structured is not None:
                raise StructuredApiError(summary, exc.code, structured) from None
            raise ApiError(summary) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = self._protect_message(str(getattr(exc, "reason", exc)))
            raise ConnectionFailure(
                f"Cannot reach Knowledge Arch at {self.base_url}: {reason}"
            ) from None

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        query_params: dict[str, object] | None = None,
        body: Any = None,
    ) -> Any:
        raw = self._request_bytes(path, method=method, query_params=query_params, body=body)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"Knowledge Arch returned invalid JSON for {path}") from exc

def _expect_list(value: Any, endpoint: str) -> list[Any]:
    if not isinstance(value, list):
        raise ApiError(f"Knowledge Arch returned an unexpected payload for {endpoint}")
    return value


def _expect_dict(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiError(f"Knowledge Arch returned an unexpected payload for {endpoint}")
    return value


def doctor(client: KnowledgeArchClient) -> dict[str, Any]:
    health = _expect_dict(client.request_json("/api/health"), "/api/health")
    collections = _expect_list(client.request_json("/api/collections"), "/api/collections")
    kb_collections = _expect_list(
        client.request_json("/api/kb/collections"), "/api/kb/collections"
    )
    config = _expect_dict(
        client.request_json("/api/config/effective"), "/api/config/effective"
    )
    return {
        "status": "ok",
        "url": client.base_url,
        "backend_status": health.get("status"),
        "local_collection_count": len(collections),
        "kb_collection_count": len(kb_collections),
        "config_sections": sorted(config),
        "plugin_llm_used": False,
    }


def _creator_names(metadata: dict[str, Any]) -> list[str]:
    creators = metadata.get("creators") or []
    result: list[str] = []
    if not isinstance(creators, list):
        return result
    for creator in creators:
        if isinstance(creator, str):
            name = creator.strip()
        elif isinstance(creator, dict):
            name = str(creator.get("name") or "").strip()
            if not name:
                name = " ".join(
                    str(creator.get(key) or "").strip()
                    for key in ("firstName", "lastName")
                ).strip()
        else:
            name = ""
        if name and name not in result:
            result.append(name)
    return result


def _document_summary(doc: dict[str, Any]) -> dict[str, Any]:
    metadata = doc.get("zotero_meta") if isinstance(doc.get("zotero_meta"), dict) else {}
    return {
        "doc_id": str(doc.get("doc_id") or ""),
        "title": str(doc.get("title") or doc.get("filename") or ""),
        "authors": _creator_names(metadata),
        "year": str(metadata.get("year") or metadata.get("date") or ""),
        "doi": str(metadata.get("doi") or ""),
        "collection": str(doc.get("collection") or ""),
        "tags": [str(tag) for tag in doc.get("tags", []) if str(tag).strip()],
        "lifecycle_state": str(doc.get("lifecycle_state") or "active"),
    }


def catalog(client: KnowledgeArchClient, query: str, limit: int) -> dict[str, Any]:
    collections = _expect_list(client.request_json("/api/collections"), "/api/collections")
    documents = _expect_list(client.request_json("/api/documents"), "/api/documents")
    terms = [term for term in re.findall(r"[\w.-]+", query.lower()) if term]
    summaries = [_document_summary(doc) for doc in documents if isinstance(doc, dict)]

    def score(summary: dict[str, Any]) -> tuple[int, str, str]:
        title = str(summary["title"]).lower()
        authors = " ".join(summary["authors"]).lower()
        fields = " ".join(
            [title, authors, str(summary["year"]), str(summary["doi"]),
             str(summary["collection"]), " ".join(summary["tags"])]
        ).lower()
        points = 0
        if query and query.lower() in title:
            points += 12
        for term in terms:
            if term in title:
                points += 5
            if term in authors:
                points += 3
            if term in fields:
                points += 1
        return (-points, title, str(summary["doc_id"]))

    summaries.sort(key=score)
    if terms:
        summaries = [item for item in summaries if score(item)[0] < 0]
    final_limit = _bounded(limit, 1, 100)
    collection_rows = [item for item in collections if isinstance(item, dict)]
    return {
        "query": query,
        "collections": [
            {
                "name": str(item.get("name") or ""),
                "coll_key": str(item.get("coll_key") or ""),
                "parent_key": str(item.get("parent_key") or ""),
            }
            for item in collection_rows
        ],
        "document_count": len(documents),
        "documents": summaries[:final_limit],
    }


def _joined_context(hit: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in hit.get("context_before", []):
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            parts.append(str(item["text"]).strip())
    if str(hit.get("text") or "").strip():
        parts.append(str(hit["text"]).strip())
    for item in hit.get("context_after", []):
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            parts.append(str(item["text"]).strip())
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return "\n\n".join(unique)


def _fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def search(
    client: KnowledgeArchClient,
    queries: list[str],
    collections: list[str],
    *,
    top_k: int,
    window: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    clean_queries = [query.strip() for query in queries if query.strip()]
    if not clean_queries:
        raise UsageError("At least one non-empty --query is required")
    if len(clean_queries) > MAX_QUERIES:
        raise UsageError(f"At most {MAX_QUERIES} queries are allowed per retrieval round")
    clean_collections = list(dict.fromkeys(collections)) or [""]
    top_k = _bounded(top_k, 1, 50)
    window = _bounded(window, 0, 10)
    limit = _bounded(limit, 1, MAX_FINAL_HITS)
    max_chars = _bounded(max_chars, 200, MAX_HIT_CHARS)

    documents = _expect_list(client.request_json("/api/documents"), "/api/documents")
    document_map = {
        str(doc.get("doc_id")): doc for doc in documents if isinstance(doc, dict)
    }
    aggregates: dict[str, dict[str, Any]] = {}
    content_keys: dict[str, str] = {}

    for query in clean_queries:
        for collection_name in clean_collections:
            payload = client.request_json(
                "/api/kb/search",
                query_params={
                    "q": query,
                    "collection": collection_name,
                    "top_k": top_k,
                    "window": window,
                },
            )
            hits = _expect_list(payload, "/api/kb/search")
            for rank, raw_hit in enumerate(hits, start=1):
                if not isinstance(raw_hit, dict):
                    continue
                text = _joined_context(raw_hit)
                if not text:
                    continue
                chunk_id = str(raw_hit.get("chunk_id") or "")
                doc_id = str(raw_hit.get("doc_id") or "")
                candidate_key = f"chunk:{chunk_id}" if chunk_id else f"text:{_fingerprint(text)}"
                fingerprint = _fingerprint(text)
                aggregate_key = content_keys.get(fingerprint, candidate_key)
                if aggregate_key not in aggregates:
                    aggregates[aggregate_key] = {
                        "score": 0.0,
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "page": raw_hit.get("page"),
                        "text": text,
                        "matched_queries": [],
                        "searched_collections": [],
                    }
                content_keys[fingerprint] = aggregate_key
                item = aggregates[aggregate_key]
                item["score"] += 1.0 / (RRF_K + rank)
                if query not in item["matched_queries"]:
                    item["matched_queries"].append(query)
                label = collection_name or "all"
                if label not in item["searched_collections"]:
                    item["searched_collections"].append(label)
                if len(text) > len(str(item["text"])):
                    item["text"] = text

    ordered = sorted(
        aggregates.values(),
        key=lambda item: (-float(item["score"]), str(item["doc_id"]), str(item["chunk_id"])),
    )[:limit]
    output_hits: list[dict[str, Any]] = []
    for rank, item in enumerate(ordered, start=1):
        doc = document_map.get(str(item["doc_id"]), {})
        summary = _document_summary(doc)
        output_hits.append(
            {
                "rank": rank,
                "rrf_score": round(float(item["score"]), 6),
                "chunk_id": item["chunk_id"],
                "doc_id": item["doc_id"],
                "title": summary["title"],
                "authors": summary["authors"],
                "year": summary["year"],
                "doi": summary["doi"],
                "source_collection": summary["collection"],
                "page": item["page"],
                "matched_queries": item["matched_queries"],
                "searched_collections": item["searched_collections"],
                "text": str(item["text"])[:max_chars],
            }
        )
    return {
        "queries": clean_queries,
        "collections": [name or "all" for name in clean_collections],
        "top_k_per_query": top_k,
        "context_window": window,
        "result_count": len(output_hits),
        "hits": output_hits,
        "plugin_llm_used": False,
    }


def ask_evidence(
    client: KnowledgeArchClient,
    question: str,
    queries: list[str],
    collection: str | None,
    *,
    mode: str,
    round_number: int,
) -> dict[str, Any]:
    """Call the no-generative-LLM Ask evidence endpoint."""
    clean_question = question.strip()
    clean_queries = [query.strip() for query in queries if query.strip()]
    if not clean_question:
        raise UsageError("--question must not be empty")
    if mode not in ASK_EVIDENCE_MODES:
        raise UsageError(f"Unsupported evidence mode: {mode}")
    if round_number < 1:
        raise UsageError("--round must be one or greater")
    if mode == "deep_thinking" and not collection:
        raise UsageError("deep_thinking requires --collection")
    payload = client.request_json(
        "/api/ask/evidence",
        method="POST",
        body={
            "question": clean_question,
            "queries": clean_queries or [clean_question],
            "collection": collection or "",
            "retrieval_mode": mode,
            "round": round_number,
        },
    )
    result = _expect_dict(payload, "/api/ask/evidence")
    if result.get("plugin_llm_used") is not False:
        raise ApiError("Evidence endpoint did not affirm plugin_llm_used=false")
    if result.get("full_text_used") is not False:
        raise ApiError("Evidence endpoint unexpectedly returned full document text")
    return result


def read_document(
    client: KnowledgeArchClient,
    doc_id: str,
    start: int,
    max_chars: int,
    *,
    intent: str,
    confirm_large_read: bool = False,
) -> dict[str, Any]:
    """Read document Markdown. ``max_chars=0`` reads to the end of the document.

    There is no client-side character cap. Reads whose returned size exceeds the
    server's confirmation threshold come back as a preview carrying **no document
    text**; surface ``total_chars`` to the user, obtain explicit consent, and only
    then retry with ``confirm_large_read=True``.
    """
    if intent not in READ_INTENTS:
        raise UsageError("read requires --intent anchored or --intent full-text")
    if start < 0:
        raise UsageError("--start must be zero or greater")
    if max_chars < 0:
        raise UsageError("--max-chars must be zero or greater (0 reads to the end)")
    query: dict[str, Any] = {"start": start, "max_chars": max_chars}
    if confirm_large_read:
        query["confirmed"] = 1
    try:
        payload = client.request_json(
            f"/api/documents/{quote(doc_id, safe='')}/content/page",
            query_params=query,
        )
    except StructuredApiError as exc:
        if (
            exc.http_status == 409
            and exc.payload.get("status") == "fulltext_confirmation_required"
        ):
            gate = redact_secrets(exc.payload)
            return {
                "status": "preview",
                "doc_id": doc_id,
                "start": start,
                "total_chars": gate.get("total_chars"),
                "requested_chars": gate.get("requested_chars"),
                "confirm_threshold_chars": gate.get("threshold_chars"),
                "estimated_tokens": gate.get("estimated_tokens"),
                "title": gate.get("title"),
                "message": gate.get("message"),
                "requires_explicit_confirmation": True,
                # Deliberately not `mutation_performed`: this is a read gate, not a
                # write gate. What the caller needs to know is that no text came back.
                "content_returned": False,
                "full_text_used": False,
                "read_intent": intent,
            }
        raise
    result = _expect_dict(payload, "document content page")
    result["read_intent"] = intent
    result["full_text_used"] = True
    return result


def graph_estimate(client: KnowledgeArchClient, collection: str) -> dict[str, Any]:
    payload = client.request_json(
        "/api/graph/build/estimate",
        method="POST",
        body={"collection": collection},
    )
    return _expect_dict(payload, "/api/graph/build/estimate")


def graph_codex_build(
    client: KnowledgeArchClient, collection: str, *, apply: bool
) -> dict[str, Any]:
    estimate = graph_estimate(client, collection)
    if not apply:
        return {
            "status": "preview",
            "collection": collection,
            "estimate": estimate,
            "plugin_llm_used": False,
            "embedding_provider_will_be_used": True,
            "mutation_performed": False,
            "requires_explicit_confirmation": True,
        }
    result = _expect_dict(
        client.request_json(
            "/api/graph/codex-build",
            method="POST",
            body={"collection": collection, "confirmed": True},
        ),
        "/api/graph/codex-build",
    )
    result["mutation_performed"] = True
    return result


def graph_next(client: KnowledgeArchClient, job_id: str) -> dict[str, Any]:
    return _expect_dict(
        client.request_json(f"/api/graph/codex-build/{quote(job_id, safe='')}/next"),
        "Codex graph next task",
    )


def graph_submit(
    client: KnowledgeArchClient, job_id: str, task_id: str, result: Any
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise UsageError("--result-json must decode to an object")
    return _expect_dict(
        client.request_json(
            f"/api/graph/codex-build/{quote(job_id, safe='')}/submit",
            method="POST",
            body={"task_id": task_id, "result": result},
        ),
        "Codex graph task submission",
    )


def graph_retry(
    client: KnowledgeArchClient, job_id: str, task_id: str
) -> dict[str, Any]:
    return _expect_dict(
        client.request_json(
            f"/api/graph/codex-build/{quote(job_id, safe='')}/retry",
            method="POST",
            body={"task_id": task_id},
        ),
        "Codex graph task retry",
    )


def graph_status(client: KnowledgeArchClient, job_id: str) -> dict[str, Any]:
    return _expect_dict(
        client.request_json(f"/api/graph/build/{quote(job_id, safe='')}"),
        "graph build status",
    )


def config_options() -> dict[str, Any]:
    return {
        "consequences": {
            "none": "saved without restart or rebuild",
            "restart": "saved; separately confirmed service restart required",
            "rebuild": (
                "saved; a separately confirmed restart plus an index rebuild are required "
                "(see the index-rebuild command)"
            ),
        },
        "writable": CONFIG_POLICIES,
        # v1.1.2 起可用：`index-rebuild --apply` 等价于 WebUI 的「重建索引」按钮。
        # 仍是变更操作，须先 preview 并取得用户明确确认。
        "rebuild_command_available": True,
        "rebuild_command": "index-rebuild",
    }


def config_show(client: KnowledgeArchClient, section: str | None) -> dict[str, Any]:
    config = _expect_dict(
        client.request_json("/api/config/effective"), "/api/config/effective"
    )
    if section:
        if section not in config:
            raise UsageError(f"Unknown effective config section: {section}")
        return {section: redact_secrets(config[section])}
    return redact_secrets(config)


def _notion_qa_string_list(value: Any, field: str) -> list[str]:
    """校验并去重 QA 标签或引用；禁止把任意对象隐式写进同步载荷。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise UsageError(f"{field} must be an array of strings")
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise UsageError(f"{field} must contain only strings")
        text = raw.strip()
        if not text:
            continue
        if len(text) > 1000:
            raise UsageError(f"{field} entries must not exceed 1000 characters")
        if text not in result:
            result.append(text)
    return result


def load_notion_qa_items(input_file: str) -> list[dict[str, Any]]:
    """读取 Codex 临时生成的 UTF-8 问答列表，供逐条写入已配置 QA 库。"""
    path = Path(input_file)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"Cannot read --input-file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UsageError("--input-file must contain valid UTF-8 JSON") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise UsageError("--input-file must be an object with an items array")
    raw_items = payload["items"]
    if not raw_items:
        raise UsageError("--input-file items must not be empty")
    if len(raw_items) > MAX_NOTION_QA_ITEMS:
        raise UsageError(f"At most {MAX_NOTION_QA_ITEMS} QA items may be saved at once")

    items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise UsageError(f"items[{index}] must be an object")
        raw_question = raw_item.get("question")
        raw_answer = raw_item.get("answer")
        if not isinstance(raw_question, str) or not raw_question.strip():
            raise UsageError(f"items[{index}].question must be a non-empty string")
        if not isinstance(raw_answer, str) or not raw_answer.strip():
            raise UsageError(f"items[{index}].answer must be a non-empty string")
        question = raw_question.strip()
        answer = raw_answer.strip()
        if len(question) > MAX_NOTION_QA_QUESTION_CHARS:
            raise UsageError(
                f"items[{index}].question exceeds {MAX_NOTION_QA_QUESTION_CHARS} characters"
            )
        if len(answer) > MAX_NOTION_QA_ANSWER_CHARS:
            raise UsageError(
                f"items[{index}].answer exceeds {MAX_NOTION_QA_ANSWER_CHARS} characters"
            )
        items.append(
            {
                "question": question,
                "answer": answer,
                "tags": _notion_qa_string_list(raw_item.get("tags"), f"items[{index}].tags"),
                "citations": _notion_qa_string_list(
                    raw_item.get("citations"), f"items[{index}].citations"
                ),
            }
        )
    return items


def notion_save_qa(client: KnowledgeArchClient, input_file: str) -> dict[str, Any]:
    """把多个原问题/回答逐条推送到已配置的 Notion QA 库。

    目标只能是 `notion_sync.qa_database_id`；文章镜像库 `database_id` 不可作为
    会话问答写入目标。每条请求由服务端先暂存 outbox，再立即尝试远端写入。
    """
    items = load_notion_qa_items(input_file)
    config = config_show(client, "notion_sync")
    notion = config.get("notion_sync")
    if not isinstance(notion, dict):
        raise ApiError("Effective config is missing notion_sync")
    if not bool(notion.get("enabled")):
        raise UsageError("Notion sync is disabled; enable it and restart the service first")
    target_database_id = str(notion.get("qa_database_id", "")).strip()
    if not target_database_id:
        raise UsageError(
            "Notion QA database is not configured (notion_sync.qa_database_id); "
            "initialize the QA database before saving conversation answers"
        )

    outcomes: list[dict[str, Any]] = []
    pushed_count = 0
    queued_count = 0
    failed_count = 0
    for index, item in enumerate(items, start=1):
        response = _expect_dict(
            client.request_json(
                "/api/notion/push-note",
                method="POST",
                body={
                    "content": item["answer"],
                    "title": item["question"],
                    "tags": item["tags"],
                    "citations": item["citations"],
                    "source": "codex",
                    "keep_local": False,
                },
            ),
            "/api/notion/push-note",
        )
        status = str(response.get("status", "error"))
        outcome = {
            "index": index,
            "question": item["question"],
            "status": status,
            "page_id": str(response.get("page_id", "")),
            "outbox_id": str(response.get("outbox_id", "")),
            "message": str(response.get("message", "")),
        }
        outcomes.append(outcome)
        if status == "success":
            pushed_count += 1
        elif status == "partial":
            queued_count += 1
        else:
            failed_count += 1

    status = "success" if not queued_count and not failed_count else "partial"
    if failed_count == len(outcomes):
        status = "error"
    return {
        "status": status,
        "target_database_id": target_database_id,
        "pushed_count": pushed_count,
        "queued_count": queued_count,
        "failed_count": failed_count,
        "items": outcomes,
    }


def config_set(
    client: KnowledgeArchClient,
    section: str,
    key: str,
    value: Any,
    *,
    apply: bool,
) -> dict[str, Any]:
    consequence = CONFIG_POLICIES.get(section, {}).get(key)
    if consequence is None:
        raise UsageError(
            f"{section}.{key} is not runtime-writable; inspect config-options "
            "instead of bypassing it"
        )
    config = _expect_dict(
        client.request_json("/api/config/effective"), "/api/config/effective"
    )
    section_values = config.get(section)
    if not isinstance(section_values, dict):
        raise ApiError(f"Effective config is missing section {section}")
    preview: dict[str, Any] = {
        "status": "preview",
        "section": section,
        "key": key,
        "current": redact_secrets(section_values.get(key)),
        "proposed": redact_secrets(value),
        "consequence": consequence,
        "restart_required": consequence in {"restart", "rebuild"},
        "rebuild_required": consequence == "rebuild",
        "mutation_performed": False,
        "requires_explicit_confirmation": True,
    }
    if not apply:
        return preview
    result = client.request_json(
        "/api/config/update",
        method="POST",
        body={"section": section, "key": key, "value": value},
    )
    preview.update(
        {
            "status": "applied",
            "mutation_performed": True,
            "requires_explicit_confirmation": False,
            "api_result": redact_secrets(result),
        }
    )
    return preview


def _vector_store_stage(client: KnowledgeArchClient) -> dict[str, Any]:
    """从能力快照里取 vector_store 阶段；拿不到时返回空 dict 而不是抛错。

    索引状态本身不该因为能力端点的形状变化而整个失败——待重建数量才是主信号。
    """
    try:
        capabilities = client.request_json("/api/capabilities")
    except ApiError:
        return {}
    if not isinstance(capabilities, dict):
        return {}
    pipeline = capabilities.get("pipeline")
    if not isinstance(pipeline, list):
        return {}
    for stage in pipeline:
        if isinstance(stage, dict) and stage.get("id") == "vector_store":
            return stage
    return {}


def index_status(client: KnowledgeArchClient) -> dict[str, Any]:
    """只读：待重建文档数、当前重建任务、以及自动重建是否已经在管这件事。"""
    count_payload = _expect_dict(
        client.request_json("/api/documents/pending-reindex-count"),
        "/api/documents/pending-reindex-count",
    )
    active_payload = _expect_dict(
        client.request_json("/api/documents/rebuild-index/active"),
        "/api/documents/rebuild-index/active",
    )
    job = active_payload.get("job")
    stage = _vector_store_stage(client)
    detail = stage.get("detail") if isinstance(stage.get("detail"), dict) else {}
    return {
        "status": "ok",
        "pending_reindex_count": int(count_payload.get("count") or 0),
        "active_job": job if isinstance(job, dict) else None,
        "backend": stage.get("current", ""),
        "vector_store_status": stage.get("status", ""),
        "compatible": detail.get("compatible"),
        "rebuild_required": detail.get("rebuild_required"),
        # 自动重建已开时，队列通常会自己排空；先看这两个字段再决定是否手动催一次。
        "auto_rebuild_enabled": detail.get("auto_rebuild_enabled"),
        "auto_rebuild_delay_seconds": detail.get("auto_rebuild_delay_seconds"),
        "reason": detail.get("reason", ""),
        "mutation_performed": False,
    }


def _wait_for_index_rebuild(
    client: KnowledgeArchClient,
    *,
    max_seconds: int,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """轮询到重建进入终态或等待上限；返回观察结果（不代表重建被取消）。

    `/api/documents/rebuild-index/active` 在 success 时返回 `job: null`（前端据此隐藏进度条），
    因此「job 消失」与「job 到达终态」都算跑完，前者按 success 汇报。
    """
    deadline = monotonic() + max_seconds
    last_job: dict[str, Any] | None = None
    while True:
        payload = _expect_dict(
            client.request_json("/api/documents/rebuild-index/active"),
            "/api/documents/rebuild-index/active",
        )
        job = payload.get("job")
        if not isinstance(job, dict):
            # 任务已消失 = 成功收尾（失败/部分失败会一直留着供重试）。
            return {"observed": "finished", "job": last_job, "final_status": "success"}
        last_job = job
        status = str(job.get("status") or "")
        if status in INDEX_REBUILD_TERMINAL_STATUSES:
            return {"observed": "finished", "job": job, "final_status": status}
        if monotonic() >= deadline:
            return {"observed": "timeout", "job": job, "final_status": status}
        sleep(INDEX_REBUILD_POLL_SECONDS)


def index_rebuild(
    client: KnowledgeArchClient,
    *,
    apply: bool,
    wait: bool = False,
    max_wait_seconds: int = INDEX_REBUILD_WAIT_MAX_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """预览或启动一次 Milvus 索引重建（等价于 WebUI 的「重建索引」按钮）。

    预览里带上 `pending_reindex_count` 与 `auto_rebuild_enabled`：多数情况下自动重建已经在
    排空队列，用户该知道这一次手动触发是不是多余的。

    服务端是全局单飞——已有重建在跑时会返回同一个 job 而非并发起第二次。
    """
    status = index_status(client)
    if not apply:
        return {
            "status": "preview",
            "pending_reindex_count": status["pending_reindex_count"],
            "active_job": status["active_job"],
            "auto_rebuild_enabled": status["auto_rebuild_enabled"],
            "compatible": status["compatible"],
            "effect": (
                "Re-embeds every pending document; a full rebuild clears the vector "
                "collection first and retrieval stays degraded until it finishes"
            ),
            "embedding_provider_will_be_used": True,
            "plugin_llm_used": False,
            "mutation_performed": False,
            "requires_explicit_confirmation": True,
        }
    started = _expect_dict(
        client.request_json("/api/documents/rebuild-index", method="POST", body={}),
        "/api/documents/rebuild-index",
    )
    result: dict[str, Any] = {
        "status": "started",
        "mutation_performed": True,
        "requires_explicit_confirmation": False,
        "pending_reindex_count_before": status["pending_reindex_count"],
        "api_result": redact_secrets(started),
    }
    if not wait:
        return result
    observation = _wait_for_index_rebuild(
        client, max_seconds=max_wait_seconds, sleep=sleep, monotonic=monotonic
    )
    result["status"] = "completed" if observation["observed"] == "finished" else "timeout"
    result["final_status"] = observation["final_status"]
    result["final_job"] = observation["job"]
    if observation["observed"] == "timeout":
        # 说清楚「不再等」不等于「已停止」，否则模型会误报重建被取消。
        result["note"] = (
            "Stopped observing after the wait limit; the rebuild is still running. "
            "Poll index-status instead of starting another rebuild."
        )
    else:
        result["pending_reindex_count_after"] = index_status(client)["pending_reindex_count"]
    return result


def restart(client: KnowledgeArchClient, *, apply: bool) -> dict[str, Any]:
    if not apply:
        return {
            "status": "preview",
            "mutation_performed": False,
            "requires_explicit_confirmation": True,
            "effect": "Knowledge Arch briefly stops answering while the service restarts",
        }
    result = client.request_json("/api/service/restart", method="POST", body={})
    return {
        "status": "applied",
        "mutation_performed": True,
        "requires_explicit_confirmation": False,
        "api_result": redact_secrets(result),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connection-file",
        type=Path,
        help=f"connection file written by the app (default: {default_connection_file()})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "connection-status", help="Show whether the running app accepts AI assistants"
    )
    subparsers.add_parser("doctor", help="Probe the connection and core read endpoints")
    add_setup_parsers(subparsers)

    catalog_parser = subparsers.add_parser(
        "catalog", help="Find collections and documents by metadata"
    )
    catalog_parser.add_argument("--query", default="")
    catalog_parser.add_argument("--limit", type=int, default=20)

    search_parser = subparsers.add_parser("search", help="Retrieve and fuse compact evidence")
    search_parser.add_argument("--query", action="append", required=True)
    scope = search_parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--collection", action="append")
    scope.add_argument("--all", dest="all_collections", action="store_true")
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.add_argument("--window", type=int, default=1)
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--max-chars", type=int, default=1600)

    ask_parser = subparsers.add_parser(
        "ask-evidence", help="Retrieve mode-aware evidence without plugin LLM synthesis"
    )
    ask_parser.add_argument("--question", required=True)
    ask_parser.add_argument("--query", action="append", default=[])
    ask_parser.add_argument("--mode", choices=ASK_EVIDENCE_MODES, default="default")
    ask_parser.add_argument("--round", type=int, default=1)
    ask_scope = ask_parser.add_mutually_exclusive_group(required=True)
    ask_scope.add_argument("--collection")
    ask_scope.add_argument("--all", dest="all_collections", action="store_true")

    read_parser = subparsers.add_parser("read", help="Read one page of document Markdown")
    read_parser.add_argument("--doc-id", required=True)
    read_parser.add_argument("--start", type=int, default=0)
    read_size_group = read_parser.add_mutually_exclusive_group()
    read_size_group.add_argument("--max-chars", type=int, default=12000)
    read_size_group.add_argument(
        "--whole",
        action="store_true",
        help="Read from --start to the end of the document (no character cap)",
    )
    read_parser.add_argument("--intent", choices=READ_INTENTS, required=True)
    read_parser.add_argument(
        "--confirm-large-read",
        action="store_true",
        help=(
            "Proceed with a read whose size exceeds the server's confirmation threshold. "
            "Only pass this after telling the user the document's total_chars and getting "
            "explicit consent. This is a read gate; --apply remains the mutation gate."
        ),
    )

    notion_save_parser = subparsers.add_parser(
        "notion-save-qa", help="Save conversation QA records to the configured Notion QA database"
    )
    notion_save_parser.add_argument(
        "--input-file",
        required=True,
        help="UTF-8 JSON object: {items:[{question,answer,tags?,citations?}]}",
    )

    graph_estimate_parser = subparsers.add_parser(
        "graph-estimate", help="Estimate a LightRAG build without mutating state"
    )
    graph_estimate_parser.add_argument("--collection", required=True)
    graph_build_parser = subparsers.add_parser(
        "graph-build", help="Preview or start a Codex-driven LightRAG build"
    )
    graph_build_parser.add_argument("--collection", required=True)
    graph_build_parser.add_argument("--apply", action="store_true")
    graph_next_parser = subparsers.add_parser("graph-next", help="Get the next Codex KG task")
    graph_next_parser.add_argument("--job-id", required=True)
    graph_submit_parser = subparsers.add_parser(
        "graph-submit", help="Submit one Codex entity/relation result"
    )
    graph_submit_parser.add_argument("--job-id", required=True)
    graph_submit_parser.add_argument("--task-id", required=True)
    graph_submit_parser.add_argument("--result-json", required=True)
    graph_retry_parser = subparsers.add_parser("graph-retry", help="Retry one failed KG task")
    graph_retry_parser.add_argument("--job-id", required=True)
    graph_retry_parser.add_argument("--task-id", required=True)
    graph_status_parser = subparsers.add_parser("graph-status", help="Show graph build status")
    graph_status_parser.add_argument("--job-id", required=True)

    show_parser = subparsers.add_parser("config-show", help="Show redacted effective configuration")
    show_parser.add_argument("--section")
    subparsers.add_parser("config-options", help="List runtime-writable settings and consequences")

    set_parser = subparsers.add_parser(
        "config-set", help="Preview or apply one safe setting change"
    )
    set_parser.add_argument("section")
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    set_parser.add_argument("--apply", action="store_true")

    subparsers.add_parser(
        "index-status", help="Show pending reindex count and any running index rebuild"
    )
    index_rebuild_parser = subparsers.add_parser(
        "index-rebuild", help="Preview or start the Milvus index rebuild (the WebUI button)"
    )
    index_rebuild_parser.add_argument("--apply", action="store_true")
    index_rebuild_parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll until the rebuild reaches a terminal status instead of returning immediately",
    )
    index_rebuild_parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=INDEX_REBUILD_WAIT_MAX_SECONDS,
        help="Stop observing after this many seconds; the rebuild itself keeps running",
    )

    restart_parser = subparsers.add_parser("restart", help="Preview or apply a service restart")
    restart_parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "connection-status":
            result = connection_status(args.connection_file)
        elif args.command == "config-options":
            result = config_options()
        else:
            client = KnowledgeArchClient.from_env(args.connection_file)
            if args.command in SETUP_COMMANDS:
                try:
                    result = run_setup_command(client, args)
                except ValueError as exc:
                    raise UsageError(str(exc)) from None
            elif args.command == "doctor":
                result = doctor(client)
            elif args.command == "catalog":
                result = catalog(client, args.query, args.limit)
            elif args.command == "search":
                collections = [""] if args.all_collections else list(args.collection or [])
                result = search(
                    client,
                    list(args.query),
                    collections,
                    top_k=args.top_k,
                    window=args.window,
                    limit=args.limit,
                    max_chars=args.max_chars,
                )
            elif args.command == "ask-evidence":
                result = ask_evidence(
                    client,
                    args.question,
                    list(args.query),
                    None if args.all_collections else args.collection,
                    mode=args.mode,
                    round_number=args.round,
                )
            elif args.command == "read":
                result = read_document(
                    client,
                    args.doc_id,
                    args.start,
                    0 if args.whole else args.max_chars,
                    intent=args.intent,
                    confirm_large_read=args.confirm_large_read,
                )
            elif args.command == "notion-save-qa":
                result = notion_save_qa(client, args.input_file)
            elif args.command == "graph-estimate":
                result = graph_estimate(client, args.collection)
            elif args.command == "graph-build":
                result = graph_codex_build(client, args.collection, apply=args.apply)
            elif args.command == "graph-next":
                result = graph_next(client, args.job_id)
            elif args.command == "graph-submit":
                try:
                    graph_result = json.loads(args.result_json)
                except json.JSONDecodeError as exc:
                    raise UsageError("--result-json must be valid JSON") from exc
                result = graph_submit(client, args.job_id, args.task_id, graph_result)
            elif args.command == "graph-retry":
                result = graph_retry(client, args.job_id, args.task_id)
            elif args.command == "graph-status":
                result = graph_status(client, args.job_id)
            elif args.command == "config-show":
                result = config_show(client, args.section)
            elif args.command == "config-set":
                result = config_set(
                    client,
                    args.section,
                    args.key,
                    parse_value(args.value),
                    apply=args.apply,
                )
            elif args.command == "index-status":
                result = index_status(client)
            elif args.command == "index-rebuild":
                result = index_rebuild(
                    client,
                    apply=args.apply,
                    wait=args.wait,
                    max_wait_seconds=args.max_wait_seconds,
                )
            elif args.command == "restart":
                result = restart(client, apply=args.apply)
            else:  # pragma: no cover - argparse guarantees a known command
                raise UsageError(f"Unknown command: {args.command}")
        print(redact_home(_compact_json(result)))
        return 0
    except ClientError as exc:
        print(
            redact_home(_compact_json({"status": "error", "error": exc.code, "message": str(exc)})),
            file=sys.stderr,
        )
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
