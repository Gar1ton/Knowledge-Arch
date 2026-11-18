"""请求内证据会话：复用候选、按方面选取、受控展开和可序列化来源关系。

ContextVar 只传递当前请求的容器；每次模式 run 新建，finally 释放，不在编排器实例上
保留可变请求状态。普通模式和外部 agent 不建立会话，沿用现有行为。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec

from kacore.managers.token_budget import TokenBudgetConfig, default_tokenizer
from kacore.pipelines.evidence_cache import EvidenceSessionCache
from kacore.pipelines.evidence_selection import (
    AspectCandidates,
    EvidenceCandidate,
    select_evidence,
)
from kacore.pipelines.evidence_views import build_evidence_views, prefix_within_budget
from kacore.utils.search_text import query_terms

if TYPE_CHECKING:
    from kacore.domain.deep_thinking import DeepThinkingOutcome
    from kacore.domain.models import DocumentChunk
    from kacore.pipelines.retrieval_orchestrator import RetrievalOutcome
    from kacore.repository.reranker.base import Reranker
    from kacore.repository.source_store.base import SourceDocumentStore

from kacore.utils.usage_ledger import current_usage, usage_request

P = ParamSpec("P")
# 补候选后每个 query 池的候选上限（与原实现一致）。
_POOL_PAD_LIMIT = 32


@dataclass
class EvidenceSession:
    """有界轮次内共享原文；seen 是发现排除集，selected 不是排除依据。

    absorb 在 gather 完成后调用，整轮子查询共用前一轮的 seen 快照。所有已见正文保留到
    请求结束（总量受编排的轮数与 candidate_k 限制），不会排除后无处复用。
    """

    cache: EvidenceSessionCache = field(default_factory=EvidenceSessionCache)
    seen: set[str] = field(default_factory=set)
    chunks: dict[str, DocumentChunk] = field(default_factory=dict)
    outcomes: list[tuple[str, RetrievalOutcome]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    selections: list[dict[str, Any]] = field(default_factory=list)
    actions: set[tuple[str, str]] = field(default_factory=set)
    requested_reads: dict[str, set[str]] = field(default_factory=dict)
    documents: dict[str, list[DocumentChunk]] = field(default_factory=dict)
    raw_scores: dict[tuple[str, str, int], float] = field(default_factory=dict)
    # 每个 query 首次参与选择时补进来的已见候选（固定下来，之后各轮沿用；见 select）。
    padding: dict[str, list[str]] = field(default_factory=dict)
    # 次方面 query → 权重（见 evidence_selection.AspectCandidates.weight）；未登记的都是主方面。
    secondary: dict[str, float] = field(default_factory=dict)
    rank_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def absorb(self, outcomes: list[tuple[str, RetrievalOutcome]]) -> None:
        """合并并行结果，保留同一证据支持多个查询的关系。"""
        for query, outcome in outcomes:
            self.outcomes.append((query, outcome))
            aspect = self.aspect_id(query)
            for chunk in outcome.chunks:
                self.seen.add(chunk.chunk_id)
                self.chunks.setdefault(chunk.chunk_id, chunk)
                edge = {"source": aspect, "target": chunk.chunk_id, "kind": "retrieved"}
                if edge not in self.edges:
                    self.edges.append(edge)

    def mark_secondary(self, queries: list[str], weight: float) -> None:
        """在 absorb 之前登记次方面；已作为主方面出现过的 query 不降级。"""
        primary = {q for q, _ in self.outcomes}
        for query in queries:
            if query not in primary:
                self.secondary[query] = weight

    @staticmethod
    def aspect_id(query: str) -> str:
        return "a-" + hashlib.sha256(query.encode()).hexdigest()[:16]

    async def select(
        self,
        reranker: Reranker,
        *,
        limit: int,
        weight: float,
        eligible: set[str] | None = None,
        pinned: set[str] | None = None,
    ) -> list[DocumentChunk]:
        """每个查询内部校准分数；缓存原始重排分数，候选变化后重新归一化。"""
        pools: dict[str, dict[str, float]] = {}
        for query, outcome in self.outcomes:
            pool = pools.setdefault(query, {})
            for rank, chunk in enumerate(outcome.chunks):
                cid = chunk.chunk_id
                if eligible is not None and cid not in eligible:
                    continue
                signal = outcome.per_chunk_signals.get(cid)
                score = signal.rrf_score if signal else 1 / (60 + rank + 1)
                pool[cid] = max(pool.get(cid, score), score)
        # 新方面先复用已有候选中包含查询词（去停用词）的段落，补到有界候选数，不新增模型规划。
        # 每个 query 只在首次出现时补一次并固定下来：以前每轮都按新进来的块重补，旧方面的池不断变、
        # 新进的块又要逐个重打分（P32 P1：deep_thinking 每题 23–34 次重排）。
        for query, pool in pools.items():
            for cid in self._padding_for(query, pool):
                if cid not in pool and (eligible is None or cid in eligible):
                    pool[cid] = 0.0
        aspects: list[AspectCandidates] = []
        async with self.rank_lock:
            if weight > 0 and not reranker.is_passthrough:
                # 各池互相独立：先收集缺分的候选，再并发重排（以前在锁内逐池串行等待）。
                await asyncio.gather(
                    *(self._rerank_missing(reranker, query, pool) for query, pool in pools.items())
                )
            for query, pool in pools.items():
                if not pool:
                    aspects.append(AspectCandidates(self.aspect_id(query), {}))
                    continue
                rrf = self._normalize(pool)
                raw = {
                    cid: self.raw_scores[query, self.chunks[cid].content_hash or cid, id(reranker)]
                    for cid in pool
                    if (query, self.chunks[cid].content_hash or cid, id(reranker))
                    in self.raw_scores
                }
                rr = self._normalize(raw)
                scores = {
                    cid: (1 - weight) * rrf[cid] + weight * rr[cid] if cid in rr else rrf[cid]
                    for cid in pool
                }
                aspects.append(
                    AspectCandidates(self.aspect_id(query), scores, self.secondary.get(query, 1.0))
                )
        candidates = {
            cid: EvidenceCandidate(
                cid,
                c.doc_id,
                str(c.metadata.get("revision_id", "")),
                c.ordinal,
                c.content_hash or cid,
                cid in (pinned or set()),
            )
            for cid, c in self.chunks.items()
            if eligible is None or cid in eligible
        }
        selected = select_evidence(aspects, candidates, max_evidence=max(0, limit))
        self.selections.append(asdict(selected))
        return [self.chunks[cid] for cid in selected.selected_ids]

    def _padding_for(self, query: str, pool: dict[str, float]) -> list[str]:
        """该 query 的补位候选：首次调用时按当时已见的块算出并缓存，之后原样返回。"""
        if query not in self.padding:
            terms = query_terms(query)
            room = max(0, _POOL_PAD_LIMIT - len(pool))
            picked: list[str] = []
            for cid, chunk in self.chunks.items():
                if len(picked) >= room:
                    break
                text = chunk.text.casefold()
                if cid not in pool and terms and any(t in text for t in terms):
                    picked.append(cid)
            self.padding[query] = picked
        return self.padding[query]

    async def _rerank_missing(self, reranker: Reranker, query: str, pool: dict[str, float]) -> None:
        """给池里还没有原始重排分的候选打分并缓存；失败不缓存，当前保留 RRF 基线。"""
        missing = [
            self.chunks[cid]
            for cid in pool
            if (query, self.chunks[cid].content_hash or cid, id(reranker)) not in self.raw_scores
        ]
        missing = list({c.content_hash or c.chunk_id: c for c in missing}.values())
        if not missing:
            return
        try:
            pair_query = prefix_within_budget(query, 128)
            allowance = max(
                1,
                TokenBudgetConfig().rerank_pair_limit - default_tokenizer().count(pair_query) - 32,
            )
            windows = [replace(c, text=prefix_within_budget(c.text, allowance)) for c in missing]
            for scored in await reranker.rerank(pair_query, windows):
                original = self.chunks[scored.chunk.chunk_id]
                key = (query, original.content_hash or original.chunk_id, id(reranker))
                self.raw_scores[key] = scored.score
        except Exception:
            # 不缓存失败；下一次可重试，当前保留 RRF 基线。
            pass

    @staticmethod
    def _normalize(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return {}
        lo, hi = min(scores.values()), max(scores.values())
        return {cid: (score - lo) / (hi - lo) if hi > lo else 1.0 for cid, score in scores.items()}

    def request_actions(self, actions: list[dict[str, str]]) -> bool:
        """只允许已在当前 scope 中读到的证据；重复动作复用视图，不再次触发纠偏。"""
        changed = False
        for action in actions:
            cid, kind = action["source_id"], action["kind"]
            if cid not in self.chunks or (cid, kind) in self.actions:
                continue
            self.actions.add((cid, kind))
            self.requested_reads.setdefault(cid, set()).add(kind)
            self.edges.append({"source": cid, "kind": kind, "reason": action["reason"]})
            changed = True
        return changed

    async def expand(
        self,
        roots: list[DocumentChunk],
        store: SourceDocumentStore,
        *,
        limit: int,
        token_budget: int = 800,
        max_depth: int = 2,
    ) -> list[DocumentChunk]:
        """条数限制 canonical 根证据；局部阅读使用独立视图，不占额外召回名额。"""
        return await build_evidence_views(
            roots[:limit],
            store,
            self.documents,
            self.edges,
            token_budget=token_budget,
            max_depth=max_depth,
            requested_reads=self.requested_reads,
        )

    def trace(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "cache_entries": {
                "retrieval": len(self.cache.retrieval),
                "embedding": len(self.cache.embedding),
                "rerank": len(self.raw_scores),
            },
            "retrieval_diagnostics": [
                {"aspect_id": self.aspect_id(q), "reason": o.fallback_reason}
                for q, o in self.outcomes
                if o.fallback_reason
            ],
            "seen_chunk_ids": sorted(self.seen),
            "edges": self.edges,
            "selections": self.selections,
            "nodes": [
                {
                    "id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "revision_id": c.metadata.get("revision_id", ""),
                    "ordinal": c.ordinal,
                }
                for c in self.chunks.values()
            ],
        }


current_evidence_session: ContextVar[EvidenceSession | None] = ContextVar(
    "evidence_session", default=None
)


def evidence_request(
    fn: Callable[P, Awaitable[DeepThinkingOutcome]],
) -> Callable[P, Awaitable[DeepThinkingOutcome]]:
    """在模式 run 边界创建并释放请求会话，结果携带后端诊断。"""

    @wraps(fn)
    @usage_request
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> DeepThinkingOutcome:
        session = EvidenceSession()
        token = current_evidence_session.set(session)
        try:
            outcome = await fn(*args, **kwargs)
            outcome.evidence_trace = session.trace()
            ledger = current_usage.get()
            if ledger is not None:
                outcome.evidence_trace["usage"] = ledger.summary()
                if ledger.calls:
                    outcome.est_total_tokens = ledger.summary()["known_tokens"]
            outcome.evidence_trace["answer_selected_ids"] = [c.chunk_id for c in outcome.evidence]
            outcome.evidence_trace["views"] = [
                {k: v for k, v in c.metadata.get("evidence_view", {}).items() if k != "text"}
                for c in outcome.evidence
            ]
            return outcome
        finally:
            current_evidence_session.reset(token)

    return wrapped


__all__ = ["EvidenceSession", "current_evidence_session", "evidence_request"]
