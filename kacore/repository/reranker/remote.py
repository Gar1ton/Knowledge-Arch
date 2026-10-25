"""线上重排器：调用连接上的 `/rerank` 接口（repository 层）。

主流重排服务共用同一形态——请求 `{model, query, documents}`，响应里每条带 `index` 与
`relevance_score`：Cohere / Jina / SiliconFlow 放在 `results`，Voyage 放在 `data`。只发
这三个必填字段：各家的可选参数（top_n / top_k / return_documents）名字不一，多发反而可能被拒。

与本地 cross-encoder 不同，网络失败多是暂时的：失败只让本次按入参顺序回退并记下原因，
下一次照常请求，不会永久退化。重排在 ask 的关键路径上，所以超时短、不重试。
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from kacore.repository.reranker.base import Reranker, ScoredChunk

if TYPE_CHECKING:
    from kacore.domain.models import DocumentChunk

logger = logging.getLogger("Reranker")

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)


class RemoteRerankError(RuntimeError):
    """响应不符合重排接口契约，或服务端返回非 200。"""


def rerank_endpoint(base_url: str) -> str:
    """连接地址 → `/rerank` 端点：保留版本 / 代理前缀，已是完整端点则不重复追加。"""
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("The rerank connection address must be an HTTP(S) address")
    path = parts.path.rstrip("/")
    if not path.endswith("/rerank"):
        path += "/rerank"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def parse_rerank_scores(data: object, count: int) -> dict[int, float]:
    """响应 → {候选下标: 分数}；越界、重复与非有限分数一律视为契约错误。"""
    if not isinstance(data, dict):
        raise RemoteRerankError("The rerank API did not return a JSON object")
    items = data.get("results", data.get("data"))
    if not isinstance(items, list):
        raise RemoteRerankError("The rerank response has no results / data list")
    scores: dict[int, float] = {}
    for item in items:
        if not isinstance(item, dict):
            raise RemoteRerankError("A rerank response item is not an object")
        index, score = item.get("index"), item.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            raise RemoteRerankError(f"Invalid rerank response index: {index!r}")
        numeric = isinstance(score, int | float) and not isinstance(score, bool)
        if not numeric or not math.isfinite(score):
            raise RemoteRerankError(f"Invalid rerank response score: {score!r}")
        if index in scores:
            raise RemoteRerankError(f"Duplicate rerank response index: {index}")
        scores[index] = float(score)
    return scores


def _passthrough(pool: list[DocumentChunk], top_n: int | None) -> list[ScoredChunk]:
    limit = len(pool) if top_n is None else min(top_n, len(pool))
    return [ScoredChunk(chunk=chunk, score=1.0 / (i + 1)) for i, chunk in enumerate(pool[:limit])]


class RemoteReranker(Reranker):
    """OpenAI 兼容连接上的 `/rerank`。密钥来自「模型供应商」连接，本机服务可以没有密钥。"""

    def __init__(
        self, *, base_url: str, model: str, api_key: str = "", max_candidates: int = 30
    ) -> None:
        self._endpoint = rerank_endpoint(base_url)
        self._model = model
        self._api_key = api_key
        self._max_candidates = max_candidates
        self._state = "idle"
        self._last_error: str | None = None

    @property
    def status(self) -> dict[str, str | bool | None]:
        return {
            "provider": "remote",
            "status": self._state,
            "model": self._model,
            "enabled": True,
            "last_error": self._last_error,
        }

    async def _request(self, query: str, documents: list[str]) -> dict[int, float]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model, "query": query, "documents": documents}
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(self._endpoint, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    raise RemoteRerankError(f"Unexpected rerank API response, HTTP {resp.status}")
                try:
                    data = await resp.json()
                except (ValueError, aiohttp.ContentTypeError):
                    raise RemoteRerankError("The rerank API did not return valid JSON") from None
        return parse_rerank_scores(data, len(documents))

    async def rerank(
        self, query: str, candidates: list[DocumentChunk], *, top_n: int | None = None
    ) -> list[ScoredChunk]:
        if not candidates:
            return []
        pool = candidates[: self._max_candidates]
        try:
            scores = await self._request(query, [chunk.text for chunk in pool])
        except Exception as exc:  # 网络 / 契约错误：本次按序回退，不抛崩 ask。
            # 网络异常可能带出含凭据的 URL：只记录自己构造的消息或异常类型。
            reason = str(exc) if isinstance(exc, RemoteRerankError) else type(exc).__name__
            logger.warning("Remote rerank failed, fallback to input order: %s", reason)
            self._state, self._last_error = "failed", reason
            return _passthrough(pool, top_n)
        self._state, self._last_error = "ready", None
        # 服务端可能只返回部分候选（各家默认 top_n 不同）：缺失的排在后面、保持原顺序。
        floor = min(scores.values(), default=0.0) - 1.0
        scored = [
            ScoredChunk(chunk=chunk, score=scores.get(i, floor - i * 1e-6))
            for i, chunk in enumerate(pool)
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored if top_n is None else scored[:top_n]


__all__ = ["RemoteRerankError", "RemoteReranker", "parse_rerank_scores", "rerank_endpoint"]
