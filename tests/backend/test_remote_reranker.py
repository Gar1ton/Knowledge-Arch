"""线上 Rerank：端点、两种响应格式、排序与失败回退、工厂、设置桥与数据流快照。

HTTP 走与线上 Embedding 契约测试同样的 aiohttp 替身，不联网。
"""
from __future__ import annotations

import pytest

from kacore.capabilities import detect_pipeline
from kacore.config import Config
from kacore.domain.models import DocumentChunk
from kacore.providers.base import ProviderProfile, api_key_ref_for
from kacore.repository.reranker import NoopReranker, build_reranker, remote
from kacore.repository.reranker.remote import (
    RemoteReranker,
    RemoteRerankError,
    parse_rerank_scores,
    rerank_endpoint,
)
from kacore.settings.store import SettingsStore


def _chunks(n: int) -> list[DocumentChunk]:
    return [DocumentChunk(f"c{i}", "d", i, f"text {i}", f"h{i}") for i in range(n)]


@pytest.fixture
def http(monkeypatch):
    queue: list[object] = []
    calls: list[tuple[str, dict]] = []

    class Response:
        def __init__(self, body, status=200):
            self.body, self.status = body, status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def json(self):
            return self.body

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return Response(*item) if isinstance(item, tuple) else Response(item)

    monkeypatch.setattr(remote.aiohttp, "ClientSession", Session)
    return queue, calls


@pytest.mark.parametrize("base, endpoint", [
    ("https://api.jina.ai/v1", "https://api.jina.ai/v1/rerank"),
    ("https://api.cohere.com/v2/", "https://api.cohere.com/v2/rerank"),
    ("https://api.siliconflow.cn/v1/rerank", "https://api.siliconflow.cn/v1/rerank"),
    ("http://localhost:8080", "http://localhost:8080/rerank"),
])
def test_endpoint_keeps_prefix_and_appends_rerank(base: str, endpoint: str) -> None:
    assert rerank_endpoint(base) == endpoint


def test_endpoint_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        rerank_endpoint("ftp://example.org")


def test_parses_results_and_data_formats() -> None:
    cohere = {"results": [
        {"index": 1, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.2},
    ]}
    voyage = {"data": [{"index": 0, "relevance_score": 0.4}]}
    assert parse_rerank_scores(cohere, 2) == {1: 0.9, 0: 0.2}
    assert parse_rerank_scores(voyage, 2) == {0: 0.4}


@pytest.mark.parametrize("body", [
    [],
    {"results": "nope"},
    {"results": [{"index": 5, "relevance_score": 1.0}]},
    {"results": [{"index": 0, "relevance_score": "high"}]},
    {"results": [{"index": 0, "relevance_score": float("nan")}]},
    {"results": [{"index": 0, "relevance_score": 1.0}, {"index": 0, "relevance_score": 0.5}]},
])
def test_rejects_malformed_responses(body: object) -> None:
    with pytest.raises(RemoteRerankError):
        parse_rerank_scores(body, 2)


async def test_rerank_sorts_by_remote_score_and_sends_bearer(http) -> None:
    queue, calls = http
    queue.append({"results": [
        {"index": 2, "relevance_score": 0.95},
        {"index": 0, "relevance_score": 0.10},
        {"index": 1, "relevance_score": 0.50},
    ]})
    reranker = RemoteReranker(base_url="https://r.example/v1", model="rr", api_key="k")

    scored = await reranker.rerank("q", _chunks(3), top_n=2)

    assert [s.chunk.chunk_id for s in scored] == ["c2", "c1"]
    url, kwargs = calls[0]
    assert url == "https://r.example/v1/rerank"
    assert kwargs["json"] == {
        "model": "rr", "query": "q", "documents": ["text 0", "text 1", "text 2"]
    }
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert reranker.status["status"] == "ready" and reranker.is_passthrough is False


async def test_partial_results_keep_missing_candidates_after_in_order(http) -> None:
    queue, _ = http
    queue.append({"results": [{"index": 3, "relevance_score": 0.8}]})
    reranker = RemoteReranker(base_url="https://r.example/v1", model="rr")

    scored = await reranker.rerank("q", _chunks(4))

    assert [s.chunk.chunk_id for s in scored] == ["c3", "c0", "c1", "c2"]


async def test_failure_falls_back_once_then_recovers(http) -> None:
    queue, calls = http
    queue.extend([("err", 503), {"results": [{"index": 1, "relevance_score": 1.0}]}])
    reranker = RemoteReranker(base_url="https://r.example/v1", model="rr")

    first = await reranker.rerank("q", _chunks(2))
    assert [s.chunk.chunk_id for s in first] == ["c0", "c1"]  # 按入参顺序回退
    assert reranker.status["status"] == "failed" and "503" in str(reranker.status["last_error"])

    second = await reranker.rerank("q", _chunks(2))
    assert [s.chunk.chunk_id for s in second] == ["c1", "c0"]  # 下一次照常请求
    assert reranker.status["status"] == "ready" and reranker.status["last_error"] is None
    assert len(calls) == 2


async def test_network_error_does_not_leak_url(http) -> None:
    queue, _ = http
    queue.append(ConnectionError("https://user:secret@r.example/v1/rerank refused"))
    reranker = RemoteReranker(base_url="https://r.example/v1", model="rr")
    await reranker.rerank("q", _chunks(1))
    assert reranker.status["last_error"] == "ConnectionError"


async def test_max_candidates_caps_the_request(http) -> None:
    queue, calls = http
    queue.append({"results": []})
    reranker = RemoteReranker(base_url="https://r.example/v1", model="rr", max_candidates=2)
    scored = await reranker.rerank("q", _chunks(5))
    assert len(calls[0][1]["json"]["documents"]) == 2 and len(scored) == 2


def test_factory_builds_remote_or_falls_back_to_noop() -> None:
    built = build_reranker(provider="remote", model="rr", base_url="https://r.example/v1")
    assert isinstance(built, RemoteReranker)
    assert isinstance(build_reranker(provider="remote", model="rr"), NoopReranker)
    no_model = build_reranker(provider="remote", model="", base_url="https://r")
    assert isinstance(no_model, NoopReranker)
    assert isinstance(build_reranker(provider="remote", model="rr", base_url="bad"), NoopReranker)


def _store_with_connection() -> SettingsStore:
    store = SettingsStore.in_memory()
    profile = ProviderProfile(
        "jina", "openai_compatible", "Jina", "https://api.jina.ai/v1", api_key_ref_for("jina")
    )
    store.set_providers([profile.to_dict()])
    store.secrets.set_secret(profile.api_key_ref, "jina-key")
    return store


def test_bridge_injects_connection_into_rerank_config() -> None:
    store = _store_with_connection()
    store.set("flow.retrieval.rerank.provider", "remote")
    store.set("flow.retrieval.rerank.remote", {"provider_id": "jina", "model": "jina-reranker-v2"})

    cfg = Config(store.to_config_raw()).get_rerank_config()

    assert (cfg.provider, cfg.model, cfg.base_url, cfg.api_key) == (
        "remote", "jina-reranker-v2", "https://api.jina.ai/v1", "jina-key"
    )


def test_bridge_turns_remote_without_connection_into_noop() -> None:
    store = _store_with_connection()
    store.set("flow.retrieval.rerank.provider", "remote")
    assert Config(store.to_config_raw()).get_rerank_config().provider == "noop"


def test_remote_rerank_is_ready_without_local_component(monkeypatch) -> None:
    monkeypatch.setattr("kacore.capabilities.module_available", lambda name: False)
    config = Config({"rerank": {"provider": "remote", "model": "rr",
                                "base_url": "https://r.example/v1"}})
    ask = {stage["id"]: stage for stage in detect_pipeline(config)}["ask"]
    assert ask["status"] == "ready" and ask["required_deps"] == []
    assert ask["detail"]["rerank_provider"] == "remote"
    assert ask["detail"]["rerank_status"] == "ready"
