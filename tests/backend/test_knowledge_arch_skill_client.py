"""Knowledge Arch skill 客户端契约测试（连接文件 + Bearer token）。"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from kacore.settings.schema import SPECS_BY_TARGET

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PATH = (
    ROOT
    / ".agents"
    / "skills"
    / "operate-knowledge-arch"
    / "scripts"
    / "knowledge_arch_client.py"
)


def _load_client_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("knowledge_arch_skill_client", CLIENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLIENT = _load_client_module()
SKILL_PATH = ROOT / ".agents" / "skills" / "operate-knowledge-arch" / "SKILL.md"


TOKEN = "agent-test-token"


class _State:
    def __init__(self) -> None:
        self.auth_headers: list[str] = []
        self.config_updates: list[dict[str, Any]] = []
        self.restart_count = 0
        self.search_queries: list[dict[str, list[str]]] = []
        self.ask_bodies: list[dict[str, Any]] = []
        self.notion_push_bodies: list[dict[str, Any]] = []
        self.graph_build_bodies: list[dict[str, Any]] = []
        self.graph_submissions: list[dict[str, Any]] = []
        # 索引重建：用例可编排待重建数与 active job 的返回序列（模拟进度推进）。
        self.rebuild_starts = 0
        self.pending_reindex_count = 0
        self.auto_rebuild_enabled = True
        self.active_jobs: list[dict[str, Any] | None] = [None]
        self.active_polls = 0
        # 全文读门禁：单个用例可调大文档长度/调小阈值来触发 409 预览。
        self.doc_chars = 100
        self.confirm_threshold = 60000


class _Handler(BaseHTTPRequestHandler):
    state: _State

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        assert isinstance(payload, dict)
        return payload

    def _reject_if_unauthenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        self.state.auth_headers.append(header)
        if header != f"Bearer {TOKEN}":
            self._json(401, {"error": "unauthorized"})
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if self._reject_if_unauthenticated():
            return
        if parsed.path == "/api/health":
            self._json(200, {"status": "ok"})
        elif parsed.path == "/api/collections":
            self._json(
                200,
                [
                    {
                        "name": "papers",
                        "coll_key": "Lpapers",
                        "parent_key": "",
                    }
                ],
            )
        elif parsed.path == "/api/kb/collections":
            self._json(200, ["papers"])
        elif parsed.path == "/api/documents":
            self._json(
                200,
                [
                    {
                        "doc_id": "d1",
                        "title": "Alpha Paper",
                        "filename": "alpha.pdf",
                        "collection": "papers",
                        "tags": ["theory"],
                        "lifecycle_state": "active",
                        "zotero_meta": {
                            "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                            "year": "2024",
                            "doi": "10.1000/alpha",
                        },
                    },
                    {
                        "doc_id": "d2",
                        "title": "Beta Notes",
                        "filename": "beta.pdf",
                        "collection": "papers",
                        "tags": ["method"],
                        "lifecycle_state": "active",
                        "zotero_meta": {"creators": ["Grace Hopper"], "year": "2023"},
                    },
                ],
            )
        elif parsed.path == "/api/kb/search":
            params = parse_qs(parsed.query, keep_blank_values=True)
            self.state.search_queries.append(params)
            query = params.get("q", [""])[0]
            long_text = "Alpha evidence " + ("x" * 700)
            alpha = {
                "chunk_id": "c1",
                "doc_id": "d1",
                "ordinal": 2,
                "text": long_text,
                "page": 7,
                "context_before": [{"text": "Alpha context"}],
                "context_after": [],
            }
            shared = {
                "chunk_id": "c2",
                "doc_id": "d2",
                "ordinal": 4,
                "text": "Shared mechanism evidence",
                "page": 9,
                "context_before": [],
                "context_after": [],
            }
            duplicate = {**alpha, "chunk_id": "different-id", "doc_id": "d2"}
            self._json(200, [alpha, shared] if "alpha" in query else [shared, duplicate])
        elif parsed.path == "/api/config/effective":
            self._json(
                200,
                {
                    "embedding": {"provider": "local", "api_key": "server-secret"},
                    "notion_sync": {
                        "enabled": True,
                        "qa_database_id": "qa-configured",
                        "sync_mode": "preserve",
                    },
                    "web_console": {"password": "server-password"},
                },
            )
        elif parsed.path == "/api/documents/d1/content/page":
            params = parse_qs(parsed.query)
            # doc_chars 让单个用例决定文档长度；默认 100 保持既有用例的期望值不变。
            content = "0123456789" * (self.state.doc_chars // 10)
            threshold = self.state.confirm_threshold
            start = int(params.get("start", ["0"])[0])
            max_chars = int(params.get("max_chars", ["12000"])[0])
            confirmed = params.get("confirmed", ["0"])[0] in {"1", "true", "yes"}
            end = len(content) if max_chars == 0 else min(len(content), start + max_chars)
            if not confirmed and end - start > threshold:
                self._json(
                    409,
                    {
                        "status": "fulltext_confirmation_required",
                        "message": "confirmation required",
                        "doc_id": "d1",
                        "title": "Long Paper",
                        "total_chars": len(content),
                        "requested_chars": end - start,
                        "threshold_chars": threshold,
                        "estimated_tokens": (end - start) // 4,
                    },
                )
                return
            self._json(
                200,
                {
                    "doc_id": "d1",
                    "start": start,
                    "end": end,
                    "total_chars": len(content),
                    "has_more": end < len(content),
                    "content": content[start:end],
                },
            )
        elif parsed.path == "/api/graph/codex-build/job-1/next":
            self._json(
                200,
                {
                    "job": {"job_id": "job-1", "status": "waiting_agent"},
                    "task": {"task_id": "task-1", "content": "Ada created Alpha."},
                    "complete": False,
                    "plugin_llm_used": False,
                },
            )
        elif parsed.path == "/api/graph/build/job-1":
            self._json(200, {"job_id": "job-1", "status": "success"})
        elif parsed.path == "/api/documents/pending-reindex-count":
            self._json(200, {"count": self.state.pending_reindex_count})
        elif parsed.path == "/api/documents/rebuild-index/active":
            index = min(self.state.active_polls, len(self.state.active_jobs) - 1)
            self.state.active_polls += 1
            self._json(200, {"job": self.state.active_jobs[index]})
        elif parsed.path == "/api/capabilities":
            self._json(
                200,
                {
                    "pipeline": [
                        {
                            "id": "vector_store",
                            "current": "milvus",
                            "status": "ready",
                            "detail": {
                                "compatible": True,
                                "rebuild_required": False,
                                "auto_rebuild_enabled": self.state.auto_rebuild_enabled,
                                "auto_rebuild_delay_seconds": 30,
                                "reason": "",
                            },
                        }
                    ]
                },
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if self._reject_if_unauthenticated():
            return
        if parsed.path == "/api/ask/evidence":
            body = self._body()
            self.state.ask_bodies.append(body)
            self._json(
                200,
                {
                    "question": body["question"],
                    "collection": body.get("collection", ""),
                    "requested_retrieval_mode": body["retrieval_mode"],
                    "actual_retrieval_mode": f"agent_{body['retrieval_mode']}",
                    "round": body["round"],
                    "queries": body["queries"],
                    "limits": {"max_queries_per_round": 4, "max_rounds": 2},
                    "evidence": [
                        {
                            "n": 1,
                            "doc_id": "d1",
                            "title": "Alpha Paper",
                            "chunk_id": "c1",
                            "text": "Alpha evidence",
                        }
                    ],
                    "requires_agent_assessment": True,
                    "plugin_llm_used": False,
                    "full_text_used": False,
                },
            )
        elif parsed.path == "/api/notion/push-note":
            body = self._body()
            self.state.notion_push_bodies.append(body)
            if body.get("title") == "暂存问题":
                self._json(
                    200,
                    {
                        "status": "partial",
                        "outbox_id": "outbox-queued",
                        "message": "queued for retry",
                    },
                )
            else:
                self._json(
                    200,
                    {
                        "status": "success",
                        "page_id": f"qa-page-{len(self.state.notion_push_bodies)}",
                        "outbox_id": f"outbox-{len(self.state.notion_push_bodies)}",
                    },
                )
        elif parsed.path == "/api/graph/build/estimate":
            body = self._body()
            self._json(
                200,
                {
                    "collection": body["collection"],
                    "documents": 2,
                    "lrag_chunks": 3,
                },
            )
        elif parsed.path == "/api/graph/codex-build":
            body = self._body()
            self.state.graph_build_bodies.append(body)
            self._json(
                200,
                {
                    "job_id": "job-1",
                    "status": "waiting_agent",
                    "plugin_llm_used": False,
                },
            )
        elif parsed.path == "/api/graph/codex-build/job-1/submit":
            body = self._body()
            self.state.graph_submissions.append(body)
            self._json(200, {"accepted": True, "plugin_llm_used": False})
        elif parsed.path == "/api/graph/codex-build/job-1/retry":
            body = self._body()
            self._json(200, {"task_id": body["task_id"], "status": "pending"})
        elif parsed.path == "/api/config/update":
            body = self._body()
            self.state.config_updates.append(body)
            rebuild = body.get("section") == "embedding"
            self._json(
                200,
                {
                    "status": "success",
                    "restart_required": rebuild,
                    "rebuild_required": rebuild,
                },
            )
        elif parsed.path == "/api/plugin/restart":
            self._body()
            self.state.restart_count += 1
            self._json(200, {"status": "restarting"})
        elif parsed.path == "/api/documents/rebuild-index":
            self._body()
            self.state.rebuild_starts += 1
            self._json(
                200,
                {"status": "started", "job": {"job_id": "milvus-1", "status": "running"}},
            )
        else:
            self._json(404, {"error": "not found"})


@contextmanager
def _mock_api() -> Iterator[tuple[_State, str]]:
    state = _State()

    class Handler(_Handler):
        pass

    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield state, f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_doctor_and_catalog_use_read_only_endpoints() -> None:
    with _mock_api() as (state, url):
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        result = CLIENT.doctor(client)
        catalog = CLIENT.catalog(client, "Ada Alpha", 20)
    assert result["status"] == "ok"
    assert result["plugin_llm_used"] is False
    assert catalog["documents"][0]["doc_id"] == "d1"
    assert catalog["documents"][0]["authors"] == ["Ada Lovelace"]
    assert state.config_updates == []


def _write_connection(path: Path, url: str, *, pid: int | None = None, **extra: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "url": url, "token": TOKEN, "pid": pid or os.getpid(), **extra}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "KNOWLEDGE_ARCH_URL", "KNOWLEDGE_ARCH_TOKEN", "KNOWLEDGE_ARCH_CONNECTION_FILE",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_bearer_token_is_sent_and_never_echoed() -> None:
    with _mock_api() as (state, url):
        result = CLIENT.doctor(CLIENT.KnowledgeArchClient(url, TOKEN))
        with pytest.raises(CLIENT.AuthenticationError, match="token") as err:
            CLIENT.doctor(CLIENT.KnowledgeArchClient(url, "stale-token"))
    assert result["backend_status"] == "ok"
    assert state.auth_headers[0] == f"Bearer {TOKEN}"
    assert TOKEN not in json.dumps(result) and "stale-token" not in str(err.value)
    assert "password" not in CLIENT.build_parser().format_help().lower()


def test_connection_failure_is_actionable() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(CLIENT.ConnectionFailure, match="Cannot reach Knowledge Arch"):
        CLIENT.doctor(CLIENT.KnowledgeArchClient(f"http://127.0.0.1:{port}", TOKEN))


def test_connection_file_is_read_on_every_call(tmp_path: Path, no_env_override: None) -> None:
    path = tmp_path / "agent-connection.json"
    assert CLIENT.connection_status(path)["status"] == "not_running"
    with pytest.raises(CLIENT.ConnectionNotConfigured, match="Allow AI assistants to connect"):
        CLIENT.resolve_connection_client(path)

    with _mock_api() as (_state, url):
        _write_connection(path, url)
        client, source = CLIENT.resolve_connection_client(path)
        assert source == "connected" and CLIENT.doctor(client)["status"] == "ok"
        status = CLIENT.connection_status(path)
    assert status == {"status": "connected"}  # 不输出地址与 token


def test_stale_or_unsupported_connection_files(tmp_path: Path, no_env_override: None) -> None:
    path = tmp_path / "agent-connection.json"
    if os.name != "nt":
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        _write_connection(path, "http://127.0.0.1:1", pid=dead.pid)
        assert CLIENT.connection_status(path)["status"] == "not_running"
    _write_connection(path, "http://127.0.0.1:1", version=99)
    with pytest.raises(CLIENT.ConnectionNotConfigured, match="Unsupported"):
        CLIENT.load_connection(path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(CLIENT.ConnectionNotConfigured, match="Cannot read"):
        CLIENT.load_connection(path)


def test_environment_override_needs_url_and_token(monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    monkeypatch.delenv("KNOWLEDGE_ARCH_TOKEN", raising=False)
    monkeypatch.setenv("KNOWLEDGE_ARCH_URL", "http://127.0.0.1:1")
    assert CLIENT.connection_status(tmp_path / "x.json")["status"] == "unconfigured"
    with pytest.raises(CLIENT.UsageError, match="KNOWLEDGE_ARCH_TOKEN"):
        CLIENT.resolve_connection_client(tmp_path / "x.json")
    monkeypatch.setenv("KNOWLEDGE_ARCH_TOKEN", TOKEN)
    client, source = CLIENT.resolve_connection_client(tmp_path / "x.json")
    assert source == "environment_override" and client.base_url == "http://127.0.0.1:1"


def test_default_connection_file_matches_the_app(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path, no_env_override: None) -> None:
    from ka_server.agent_access import connection_file_for

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert CLIENT.default_connection_file() == connection_file_for(None, tmp_path)
    monkeypatch.setenv("KNOWLEDGE_ARCH_CONNECTION_FILE", str(tmp_path / "custom.json"))
    assert CLIENT.connection_file_path() == tmp_path / "custom.json"


def test_skill_requires_explicit_connection_and_external_authorization() -> None:
    """分发 Skill 不得包含机器端口，并锁定首次连接与联网授权边界。"""
    source = SKILL_PATH.read_text(encoding="utf-8")
    assert "9821" not in source
    assert "connection-status" in source
    assert "connection-setup" not in source and "password" not in source.lower()
    assert "unless the user explicitly grants permission" in source
    assert "request-secret" in source and "references/setup.md" in source
    assert "notion-save-qa" in source
    assert "qa_database_id" in source
    assert "Never create a loose Notion page" in source


def test_notion_save_qa_uses_configured_database_and_preserves_partial_results(
    tmp_path: Path,
) -> None:
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "question": "问题一",
                        "answer": "回答一",
                        "tags": ["conversation"],
                        "citations": ["d1"],
                    },
                    {"question": "暂存问题", "answer": "回答二"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with _mock_api() as (state, url):
        result = CLIENT.notion_save_qa(CLIENT.KnowledgeArchClient(url, TOKEN), str(payload_path))

    assert result["status"] == "partial"
    assert result["target_database_id"] == "qa-configured"
    assert result["pushed_count"] == 1
    assert result["queued_count"] == 1
    assert result["failed_count"] == 0
    assert state.notion_push_bodies == [
        {
            "content": "回答一",
            "title": "问题一",
            "tags": ["conversation"],
            "citations": ["d1"],
            "source": "codex",
            "keep_local": False,
        },
        {
            "content": "回答二",
            "title": "暂存问题",
            "tags": [],
            "citations": [],
            "source": "codex",
            "keep_local": False,
        },
    ]


def test_notion_save_qa_requires_configured_qa_database(tmp_path: Path) -> None:
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text(
        json.dumps({"items": [{"question": "Q", "answer": "A"}]}),
        encoding="utf-8",
    )

    class MissingQaDatabaseClient:
        def request_json(self, path: str, **_kwargs: Any) -> dict[str, Any]:
            assert path == "/api/config/effective"
            return {"notion_sync": {"enabled": True, "qa_database_id": ""}}

    with pytest.raises(CLIENT.UsageError, match="qa_database_id"):
        CLIENT.notion_save_qa(MissingQaDatabaseClient(), str(payload_path))


def test_search_fuses_queries_deduplicates_content_and_caps_text() -> None:
    with _mock_api() as (state, url):
        result = CLIENT.search(
            CLIENT.KnowledgeArchClient(url, TOKEN),
            ["alpha concept", "beta mechanism"],
            [""],
            top_k=8,
            window=1,
            limit=99,
            max_chars=250,
        )
    assert result["collections"] == ["all"]
    assert result["result_count"] == 2
    assert {hit["chunk_id"] for hit in result["hits"]} == {"c1", "c2"}
    assert all(len(hit["text"]) <= 250 for hit in result["hits"])
    alpha = next(hit for hit in result["hits"] if hit["chunk_id"] == "c1")
    assert alpha["title"] == "Alpha Paper"
    assert alpha["page"] == 7
    assert len(alpha["matched_queries"]) == 2
    assert all(params["collection"] == [""] for params in state.search_queries)


def test_search_rejects_unbounded_query_fanout() -> None:
    with _mock_api() as (_state, url):
        with pytest.raises(CLIENT.UsageError, match="At most 4 queries"):
            CLIENT.search(
                CLIENT.KnowledgeArchClient(url, TOKEN),
                ["1", "2", "3", "4", "5"],
                [""],
                top_k=8,
                window=1,
                limit=10,
                max_chars=1600,
            )


def test_ask_evidence_preserves_agent_reasoning_boundary() -> None:
    with _mock_api() as (state, url):
        result = CLIENT.ask_evidence(
            CLIENT.KnowledgeArchClient(url, TOKEN),
            "Compare Alpha and Beta",
            ["alpha mechanism", "beta mechanism"],
            "papers",
            mode="enhanced",
            round_number=1,
        )
    assert result["actual_retrieval_mode"] == "agent_enhanced"
    assert result["requires_agent_assessment"] is True
    assert result["plugin_llm_used"] is False
    assert result["full_text_used"] is False
    assert state.ask_bodies == [
        {
            "question": "Compare Alpha and Beta",
            "queries": ["alpha mechanism", "beta mechanism"],
            "collection": "papers",
            "retrieval_mode": "enhanced",
            "round": 1,
        }
    ]


def test_read_document_pages_without_dumping_full_text() -> None:
    with _mock_api() as (_state, url):
        result = CLIENT.read_document(
            CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 10, 25, intent="anchored"
        )
    assert result == {
        "doc_id": "d1",
        "start": 10,
        "end": 35,
        "total_chars": 100,
        "has_more": True,
        "content": "0123456789012345678901234",
        "read_intent": "anchored",
        "full_text_used": True,
    }


def test_read_document_requires_explicit_intent() -> None:
    with _mock_api() as (_state, url):
        with pytest.raises(CLIENT.UsageError, match="read requires --intent"):
            CLIENT.read_document(
                CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 0, 10, intent=""
            )


def test_distribution_policy_snapshot_matches_config_truth_source() -> None:
    expected: dict[str, dict[str, str]] = {}
    for (section, key), spec in SPECS_BY_TARGET.items():
        if spec.kind != "secret":
            expected.setdefault(section, {})[key] = spec.consequence
    # 旧 embedding.provider / model 写入经 api 的过渡映射仍可用（后果为重建）。
    expected.setdefault("embedding", {}).update({"provider": "rebuild", "model": "rebuild"})
    assert CLIENT.CONFIG_POLICIES == expected


def test_config_preview_apply_whitelist_and_redaction() -> None:
    with _mock_api() as (state, url):
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        preview = CLIENT.config_set(client, "embedding", "provider", "external", apply=False)
        assert state.config_updates == []
        assert preview["consequence"] == "rebuild"
        assert preview["mutation_performed"] is False

        applied = CLIENT.config_set(client, "embedding", "provider", "external", apply=True)
        shown = CLIENT.config_show(client, None)
        with pytest.raises(CLIENT.UsageError, match="not runtime-writable"):
            CLIENT.config_set(client, "web_console", "password", "leak", apply=True)

    assert state.config_updates == [
        {"section": "embedding", "key": "provider", "value": "external"}
    ]
    assert applied["mutation_performed"] is True
    assert shown["embedding"]["api_key"] == "[redacted]"
    assert shown["web_console"]["password"] == "[redacted]"
    assert "server-secret" not in json.dumps(shown)
    assert "server-password" not in json.dumps(shown)


def test_restart_has_a_separate_apply_gate() -> None:
    with _mock_api() as (state, url):
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        preview = CLIENT.restart(client, apply=False)
        assert preview["requires_explicit_confirmation"] is True
        assert state.restart_count == 0
        applied = CLIENT.restart(client, apply=True)
    assert applied["mutation_performed"] is True
    assert state.restart_count == 1


def test_index_status_is_read_only_and_reports_auto_rebuild() -> None:
    """先看状态再决定要不要重建：多数时候自动重建已经在排空队列。"""
    with _mock_api() as (state, url):
        state.pending_reindex_count = 4
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        result = CLIENT.index_status(client)
    assert result["pending_reindex_count"] == 4
    assert result["auto_rebuild_enabled"] is True
    assert result["auto_rebuild_delay_seconds"] == 30
    assert result["compatible"] is True
    assert result["mutation_performed"] is False
    assert state.rebuild_starts == 0  # 只读命令绝不触发重建


def test_index_rebuild_has_a_separate_apply_gate() -> None:
    with _mock_api() as (state, url):
        state.pending_reindex_count = 2
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        preview = CLIENT.index_rebuild(client, apply=False)
        assert preview["status"] == "preview"
        assert preview["requires_explicit_confirmation"] is True
        assert preview["pending_reindex_count"] == 2
        assert preview["embedding_provider_will_be_used"] is True
        assert state.rebuild_starts == 0  # 预览不得触发

        applied = CLIENT.index_rebuild(client, apply=True)
    assert applied["status"] == "started"
    assert applied["mutation_performed"] is True
    assert state.rebuild_starts == 1


def test_index_rebuild_wait_reports_terminal_status() -> None:
    with _mock_api() as (state, url):
        state.pending_reindex_count = 1
        state.active_jobs = [
            {"job_id": "milvus-1", "status": "running", "progress_percent": 40},
            {"job_id": "milvus-1", "status": "partial_failure", "failed_docs": 1},
        ]
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        result = CLIENT.index_rebuild(
            client, apply=True, wait=True, sleep=lambda _seconds: None
        )
    assert result["status"] == "completed"
    assert result["final_status"] == "partial_failure"
    assert result["final_job"]["failed_docs"] == 1
    assert state.rebuild_starts == 1


def test_index_rebuild_wait_treats_vanished_job_as_success() -> None:
    """成功后 /active 返回 job: null（前端据此隐藏进度条）——不能被当成失败。"""
    with _mock_api() as (_state, url):
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        result = CLIENT.index_rebuild(
            client, apply=True, wait=True, sleep=lambda _seconds: None
        )
    assert result["status"] == "completed"
    assert result["final_status"] == "success"


def test_index_rebuild_wait_timeout_does_not_claim_cancellation() -> None:
    """回归锁：等待上限只是停止观察，重建仍在跑；措辞不得让模型误报为已取消。"""
    ticks = iter([0.0, 0.0, 100.0, 200.0])
    with _mock_api() as (state, url):
        state.active_jobs = [{"job_id": "milvus-1", "status": "running"}]
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        result = CLIENT.index_rebuild(
            client,
            apply=True,
            wait=True,
            max_wait_seconds=1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(ticks),
        )
    assert result["status"] == "timeout"
    assert result["final_status"] == "running"
    assert "still running" in result["note"]
    assert state.rebuild_starts == 1  # 超时不得重复触发


def test_config_options_advertises_the_rebuild_command() -> None:
    options = CLIENT.config_options()
    assert options["rebuild_command_available"] is True
    assert options["rebuild_command"] == "index-rebuild"


def test_codex_graph_build_is_previewed_and_uses_task_protocol() -> None:
    with _mock_api() as (state, url):
        client = CLIENT.KnowledgeArchClient(url, TOKEN)
        preview = CLIENT.graph_codex_build(client, "papers", apply=False)
        assert preview["mutation_performed"] is False
        assert preview["embedding_provider_will_be_used"] is True
        assert state.graph_build_bodies == []

        started = CLIENT.graph_codex_build(client, "papers", apply=True)
        task = CLIENT.graph_next(client, "job-1")
        submitted = CLIENT.graph_submit(
            client,
            "job-1",
            "task-1",
            {
                "entities": [
                    {
                        "entity_name": "Ada",
                        "entity_type": "PERSON",
                        "description": "Researcher",
                    }
                ],
                "relationships": [],
            },
        )
        status = CLIENT.graph_status(client, "job-1")

    assert started["job_id"] == "job-1"
    assert task["task"]["task_id"] == "task-1"
    assert submitted["accepted"] is True
    assert status["status"] == "success"
    assert state.graph_build_bodies == [{"collection": "papers", "confirmed": True}]
    assert state.graph_submissions[0]["task_id"] == "task-1"


def test_client_exposes_only_agent_evidence_and_guarded_graph_builds() -> None:
    source = CLIENT_PATH.read_text(encoding="utf-8")
    assert '"/api/ask/evidence"' in source
    assert '"/api/ask"' not in source
    assert '"/api/graph/codex-build"' in source
    assert '"/api/graph/build"' not in source
    assert '/content/page"' in source


def test_index_rebuild_endpoint_is_reachable_but_gated() -> None:
    """v1.1.2 起索引重建对 skill 开放（此前这里断言的是「端点不得出现」）。

    开放的是**受 `--apply` 门禁保护**的调用，不是随手就能跑：POST 只能出现在
    `index_rebuild()` 的 apply 分支里，且该函数必须在无 apply 时先返回 preview。
    """
    import inspect

    source = CLIENT_PATH.read_text(encoding="utf-8")
    assert '"/api/documents/rebuild-index"' in source

    body = inspect.getsource(CLIENT.index_rebuild)
    preview_index = body.index('"status": "preview"')
    post_index = body.index('"/api/documents/rebuild-index"')
    assert preview_index < post_index  # 未确认时先 return preview，够不到 POST
    assert "if not apply:" in body


def test_fresh_process_cli_notion_save_qa(tmp_path: Path) -> None:
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text(
        json.dumps({"items": [{"question": "CLI 问题", "answer": "CLI 回答"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with _mock_api() as (state, url):
        env = os.environ.copy()
        env["KNOWLEDGE_ARCH_URL"] = url
        env["KNOWLEDGE_ARCH_TOKEN"] = TOKEN
        saved = subprocess.run(
            [
                sys.executable,
                "-I",  # 与应用生成的命令一致：隔离模式下也要能找到同目录的 setup 模块
                str(CLIENT_PATH),
                "notion-save-qa",
                "--input-file",
                str(payload_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
        )

    assert saved.returncode == 0, saved.stderr
    assert json.loads(saved.stdout)["status"] == "success"
    assert state.notion_push_bodies[0]["title"] == "CLI 问题"


def test_fresh_process_cli_research_and_config_preview() -> None:
    """用隔离 Python 进程模拟 Codex 只读研究与配置预览两条真实命令链。"""
    with _mock_api() as (state, url):
        env = os.environ.copy()
        env["KNOWLEDGE_ARCH_URL"] = url
        env["KNOWLEDGE_ARCH_TOKEN"] = TOKEN
        research = subprocess.run(
            [
                sys.executable,
                str(CLIENT_PATH),
                "ask-evidence",
                "--question",
                "What is Alpha?",
                "--query",
                "alpha concept",
                "--mode",
                "default",
                "--all",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
        )
        preview = subprocess.run(
            [
                sys.executable,
                str(CLIENT_PATH),
                "config-set",
                "embedding",
                "provider",
                "external",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
        )

    assert research.returncode == 0, research.stderr
    assert json.loads(research.stdout)["plugin_llm_used"] is False
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["status"] == "preview"
    assert state.config_updates == []


def test_read_document_small_document_needs_no_confirmation() -> None:
    """低于阈值零摩擦：整篇读（--whole → max_chars=0）也直接返回正文。"""
    with _mock_api() as (_state, url):
        result = CLIENT.read_document(
            CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 0, 0, intent="full-text"
        )
    assert result["full_text_used"] is True
    assert len(result["content"]) == 100
    assert "requires_explicit_confirmation" not in result


def test_read_document_large_document_previews_before_returning_text() -> None:
    """超阈值单次读取只回预览，**绝不夹带正文**。"""
    with _mock_api() as (state, url):
        state.doc_chars = 200_000
        result = CLIENT.read_document(
            CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 0, 0, intent="full-text"
        )
    assert result["status"] == "preview"
    assert result["requires_explicit_confirmation"] is True
    assert result["content_returned"] is False
    assert result["full_text_used"] is False
    assert result["total_chars"] == 200_000
    assert result["confirm_threshold_chars"] == 60000
    assert "content" not in result


def test_read_document_confirmed_returns_whole_document() -> None:
    """确认后拿到全文；长度 > 40000 证明旧的客户端硬上限已拆除。"""
    with _mock_api() as (state, url):
        state.doc_chars = 200_000
        result = CLIENT.read_document(
            CLIENT.KnowledgeArchClient(url, TOKEN),
            "d1",
            0,
            0,
            intent="full-text",
            confirm_large_read=True,
        )
    assert result["full_text_used"] is True
    assert len(result["content"]) == 200_000
    assert result["has_more"] is False


def test_read_document_paging_below_threshold_never_trips_the_gate() -> None:
    """门禁按单次返回量判定：长文档的小分页读不应被拦。"""
    with _mock_api() as (state, url):
        state.doc_chars = 200_000
        result = CLIENT.read_document(
            CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 0, 12000, intent="anchored"
        )
    assert result["full_text_used"] is True
    assert len(result["content"]) == 12000


def test_read_document_rejects_negative_max_chars() -> None:
    with _mock_api() as (_state, url):
        with pytest.raises(CLIENT.UsageError, match="zero or greater"):
            CLIENT.read_document(
                CLIENT.KnowledgeArchClient(url, TOKEN), "d1", 0, -1, intent="anchored"
            )


def test_output_never_contains_the_home_directory(monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    text = json.dumps({"dir": f"{home}/Zotero", "same": str(home), "other": f"{home}2/x"})
    redacted = CLIENT.redact_home(text)
    assert f"{home}/" not in redacted and f'"{home}"' not in redacted
    assert '"~/Zotero"' in redacted and '"same": "~"' in redacted
    assert f"{home}2/x" in redacted  # 只是前缀相同的其它目录不改


def test_cli_output_is_redacted(tmp_path: Path) -> None:
    with _mock_api() as (_state, url):
        env = os.environ.copy()
        env["KNOWLEDGE_ARCH_URL"] = url
        env["KNOWLEDGE_ARCH_TOKEN"] = TOKEN
        env["HOME"] = str(Path.home())
        result = subprocess.run(
            [sys.executable, "-I", str(CLIENT_PATH), "detect"],
            check=False, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20,
        )
    assert result.returncode == 0, result.stderr
    assert str(Path.home()) not in result.stdout
    assert "system" not in json.loads(result.stdout)
