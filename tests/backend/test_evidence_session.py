"""证据会话行为回归：共享证据、隔离、缓存及有界来源展开。"""

from __future__ import annotations

import asyncio

import pytest

from kacore.domain.deep_thinking import DeepThinkingOutcome
from kacore.domain.models import DocumentChunk
from kacore.pipelines.evidence_cache import SingleFlightCache
from kacore.pipelines.evidence_session import (
    EvidenceSession,
    current_evidence_session,
    evidence_request,
)
from kacore.pipelines.retrieval_orchestrator import ChunkSignal, RetrievalOutcome
from kacore.repository.reranker.noop import NoopReranker


def chunk(cid, doc, ordinal=0, text=None):
    return DocumentChunk(cid, doc, ordinal, text or cid, cid, {"revision_id": "r1"})


@pytest.mark.asyncio
async def test_book_facets_and_paper_survive_without_document_quota():
    session = EvidenceSession()
    for q, c in [
        ("history", chunk("h", "book")),
        ("mechanism", chunk("m", "book", 50)),
        ("critique", chunk("c", "book", 200)),
        ("experiment", chunk("p", "paper")),
    ]:
        session.absorb(
            [(q, RetrievalOutcome([c], per_chunk_signals={c.chunk_id: ChunkSignal(1, False)}))]
        )
    final = await session.select(NoopReranker(), limit=4, weight=0)
    assert [c.chunk_id for c in final] == ["h", "m", "c", "p"]
    assert session.seen == {"h", "m", "c", "p"}
    assert len(session.selections[-1]["aspect_coverage"]) == 4


@pytest.mark.asyncio
async def test_shared_chunk_is_stored_once_and_has_two_aspect_edges():
    session = EvidenceSession()
    c = chunk("c", "book")
    session.absorb([("q1", RetrievalOutcome([c])), ("q2", RetrievalOutcome([c]))])
    assert len(session.chunks) == 1
    assert len(session.edges) == 2
    final = await session.select(NoopReranker(), limit=1, weight=0)
    assert final == [c]
    assert all(ids == ["c"] for ids in session.selections[-1]["aspect_coverage"].values())


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_share_seen():
    @evidence_request
    async def run(cid):
        session = current_evidence_session.get()
        session.absorb([("q", RetrievalOutcome([chunk(cid, "doc")]))])
        await asyncio.sleep(0)
        return DeepThinkingOutcome()

    a, b = await asyncio.gather(run("a"), run("b"))
    assert a.evidence_trace["seen_chunk_ids"] == ["a"]
    assert b.evidence_trace["seen_chunk_ids"] == ["b"]
    assert current_evidence_session.get() is None


@pytest.mark.asyncio
async def test_cancel_waiter_does_not_cancel_singleflight_owner():
    cache = SingleFlightCache()
    ready, finish = asyncio.Event(), asyncio.Event()

    async def compute():
        ready.set()
        await finish.wait()
        return 42

    owner = asyncio.create_task(cache.get_or_compute("k", compute))
    await ready.wait()
    waiter = asyncio.create_task(cache.get_or_compute("k", compute))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    finish.set()
    assert await owner == 42
    assert await cache.get_or_compute("k", compute) == 42


@pytest.mark.asyncio
async def test_expansion_is_same_revision_bounded_and_not_causal():
    session = EvidenceSession()
    a, b, c = [chunk(str(i), "book", i, "unfinished text") for i in range(3)]
    wrong = chunk("wrong", "book", 3)
    wrong.metadata["revision_id"] = "other"

    class Store:
        async def list_chunks(self, doc):
            return [a, b, c, wrong]

    final = await session.expand([a], Store(), limit=8, max_depth=4)
    # 续接进入独立视图，保留 canonical 根的 ID、正文和 hash。
    assert len(final) == 1
    assert final[0].text == a.text and final[0].content_hash == a.content_hash
    assert [s["chunk_id"] for s in final[0].metadata["evidence_view"]["sources"]] == ["0", "1", "2"]
    assert [e["depth"] for e in session.edges] == [1, 2]
    assert all(e["kind"] == "adjacent_after" for e in session.edges)


class _CountingReranker:
    """记录每次 rerank 的 (query, 候选 id) 与同时在途的调用数。"""

    is_passthrough = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def rerank(self, query, candidates):
        from kacore.repository.reranker.base import ScoredChunk

        self.calls.append((query, [c.chunk_id for c in candidates]))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return [ScoredChunk(chunk=c, score=1.0) for c in candidates]


def _outcome(*chunks):
    signals = {c.chunk_id: ChunkSignal(1, False) for c in chunks}
    return RetrievalOutcome(list(chunks), per_chunk_signals=signals)


@pytest.mark.asyncio
async def test_query_pools_are_reranked_concurrently():
    session = EvidenceSession()
    session.absorb([(f"q{i}", _outcome(chunk(f"c{i}", "d"))) for i in range(4)])
    reranker = _CountingReranker()
    await session.select(reranker, limit=4, weight=0.5)
    assert len(reranker.calls) == 4 and reranker.max_in_flight == 4


@pytest.mark.asyncio
async def test_padding_is_fixed_at_first_use_and_scores_are_not_recomputed():
    session = EvidenceSession()
    session.absorb([("attention limitation", _outcome(chunk("a", "d", text="attention head")))])
    reranker = _CountingReranker()
    await session.select(reranker, limit=4, weight=0.5)
    first = list(reranker.calls)
    # 下一轮进来一个也含查询词的新块：旧 query 的池不再补它，旧候选也不重打分。
    session.absorb([("other topic", _outcome(chunk("b", "d", text="the limitation of attention")))])
    await session.select(reranker, limit=4, weight=0.5)
    later = reranker.calls[len(first):]
    assert all(query != "attention limitation" for query, _ in later)
    assert session.padding["attention limitation"] == []


@pytest.mark.asyncio
async def test_padding_ignores_stopwords():
    session = EvidenceSession()
    session.absorb([("seen", _outcome(chunk("x", "d", text="the of and what is this")))])
    session.absorb([("what is the limitation", _outcome(chunk("y", "d", text="limitation found")))])
    await session.select(NoopReranker(), limit=4, weight=0)
    assert session.padding["what is the limitation"] == []          # "the/what/is" 不再命中 x
    assert session.padding["seen"] == []
