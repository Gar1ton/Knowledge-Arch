"""联网补充：回答时去学术索引找外部文献，核验后作为可引用证据交给回答合成（managers 层）。

只在对话栏打开「联网补充」（或 AI 助手经 skill 取得用户同意）时运行。一次运行的顺序：
  ① 写检索词：用户配置的 research 模型读问题与库内证据，只为库里缺的部分写词
     （skill 路径由助手给词）；
  ② 检索：各已启用索引并发查（索引只给书目数据）；
  ③ 归并：按标识归并（`merge_hits`，不按标题猜同一篇）；
  ④ 核验：前若干篇拿编号去第二个索引查（DOI 查不到再问 doi.org），并顺带补上结构化作者；
  ⑤ 挑选：模型从可引用的候选里挑最能支撑回答的几篇（skill 路径交给助手自己挑）；
  ⑥ 读取：depth=fulltext 时下载开放获取全文、按页切段、用 Rerank 选段；否则用摘要。
可信与否只由 `trust_level` 按索引数据决定，模型不参与。出处（检索词、索引、排名、请求地址、
字段来源、引用段落）由 `persist` 写进账本（迁移 029）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.adapters.scholarly.base import (
    ENGINE_ARXIV,
    ENGINE_CROSSREF,
    ENGINE_DATACITE,
    ENGINE_OPENALEX,
    ENGINE_OPENLIBRARY,
    ENGINE_SEMANTIC_SCHOLAR,
    ScholarlyIndex,
)
from kacore.domain.external_ledger import (
    Capture,
    CaptureHit,
    Citation,
    FieldOrigin,
    StoredSource,
)
from kacore.domain.models import DocumentChunk
from kacore.domain.web_sources import (
    CITABLE,
    PID_ARXIV,
    PID_DOI,
    PID_ISBN,
    PID_PMID,
    IndexHit,
    SourceRecord,
    Trust,
    merge_hits,
    trust_level,
)
from kacore.managers.external_fulltext import FulltextCache, page_passages
from kacore.pipelines.deep_thinking_prompts import JsonContractError
from kacore.pipelines.external_prompts import (
    PLAN_SYSTEM,
    SELECT_SYSTEM,
    Candidate,
    SearchPlan,
    parse_plan,
    parse_selection,
    render_plan_prompt,
    render_select_prompt,
)
from kacore.pipelines.llm_json import llm_json_call

if TYPE_CHECKING:
    from kacore.adapters.scholarly.registries import DoiResolver
    from kacore.config import ExternalEvidenceConfig
    from kacore.repository.reranker.base import Reranker
    from kacore.repository.source_store.external import ExternalSourceRepository

logger = logging.getLogger("ExternalEvidence")

EXTERNAL_DOC_PREFIX = "ext:"
_PASSAGES_PER_SOURCE = 2
_LOOKUP_CONCURRENCY = 4
_LOOKUPS_PER_RECORD = 2


class ExternalEvidenceUnavailable(RuntimeError):
    """没有可用的索引（全部关闭），或全部索引都失败。"""


@dataclass
class IndexSet:
    """一次运行可用的索引：search 是检索用（用户启用的），lookup 是核验用（含 DataCite）。"""

    search: list[ScholarlyIndex]
    lookup: dict[str, ScholarlyIndex]
    resolver: DoiResolver | None = None


@dataclass
class ExternalPassage:
    """进入回答的一段外部证据：page 为 None 表示用的是摘要。"""

    source_id: str
    text: str
    page: int | None = None
    file_sha256: str = ""


@dataclass
class ExternalBundle:
    """一次联网补充的结果与出处（persist 之前只在内存里）。"""

    capture_id: str
    question: str
    started_at: str
    queries: list[str]
    gaps: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    records: list[SourceRecord] = field(default_factory=list)
    trust: dict[str, Trust] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    passages: list[ExternalPassage] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    request_urls: dict[tuple[str, str], str] = field(default_factory=dict)

    def record(self, source_id: str) -> SourceRecord | None:
        return next((r for r in self.records if r.source_id == source_id), None)


class ExternalEvidence:
    """契约：
    - `collect` 不写库；索引全部关闭或全部失败时抛 ExternalEvidenceUnavailable，
      部分索引失败只记进 bundle.errors；模型不可用时退回「原问题当检索词、按排名取前几篇」。
    - `persist` 一次写入 capture、文献、命中、字段出处与引用段落；is_new 按同一对话先前的命中计算。
    """

    def __init__(
        self,
        *,
        config_provider: Callable[[], ExternalEvidenceConfig],
        index_factory: Callable[[ExternalEvidenceConfig], IndexSet],
        store: ExternalSourceRepository,
        llm_adapter: Any | None,
        reranker: Reranker | None,
        fulltext: FulltextCache,
    ) -> None:
        self._config = config_provider
        self._indexes = index_factory
        self._store = store
        self._llm = llm_adapter
        self._reranker = reranker
        self._fulltext = fulltext

    # ── 一次运行 ────────────────────────────────────────────

    async def collect(
        self,
        question: str,
        local_excerpts: Sequence[tuple[str, str]],
        *,
        queries: Sequence[str] | None = None,
        use_model: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> ExternalBundle:
        cfg = self._config()
        indexes = self._indexes(cfg)
        if not indexes.search:
            raise ExternalEvidenceUnavailable("no scholarly index is enabled")
        step = progress or (lambda _stage: None)
        bundle = ExternalBundle(capture_id=f"c_{uuid.uuid4().hex[:12]}", question=question,
                                started_at=_now(), queries=[],
                                engines=[i.name for i in indexes.search])
        step("external_plan")
        if queries:
            plan = SearchPlan((), tuple(dict.fromkeys(q.strip() for q in queries if q.strip())))
        else:
            plan = await self._plan(question, local_excerpts, cfg) if use_model else None
            plan = plan or SearchPlan((), (question,))
        bundle.queries, bundle.gaps = list(plan.queries[: cfg.max_queries]), list(plan.gaps)
        step("external_search")
        hits = await self._search_all(indexes, bundle, cfg.per_query)
        if not hits and len(bundle.errors) == len(indexes.search):
            raise ExternalEvidenceUnavailable("; ".join(bundle.errors.values()))
        bundle.records = _by_best_rank(merge_hits(hits))
        step("external_verify")
        await self._verify(indexes, bundle.records[: cfg.max_candidates])
        bundle.trust = {r.source_id: trust_level(r) for r in bundle.records}
        citable = [r for r in bundle.records[: cfg.max_candidates]
                   if bundle.trust[r.source_id] in CITABLE and _text_of(r)]
        step("external_select")
        if use_model and not queries:
            chosen = await self._select(question, bundle.gaps, citable, cfg.max_sources)
        else:
            chosen = None
        if chosen is None:
            chosen = [(r.source_id, "") for r in citable[: cfg.max_sources]]
        bundle.selected = [sid for sid, _ in chosen]
        bundle.reasons = {sid: why for sid, why in chosen if why}
        step("external_read")
        bundle.passages = await self._read(bundle, question, cfg)
        return bundle

    async def _plan(self, question: str, local_excerpts: Sequence[tuple[str, str]],
                    cfg: ExternalEvidenceConfig) -> SearchPlan | None:
        if self._llm is None:
            return None
        prompt = render_plan_prompt(question, list(local_excerpts)[:8], cfg.max_queries)
        try:
            plan, _calls, _tokens = await llm_json_call(
                self._llm, prompt, PLAN_SYSTEM, lambda raw: parse_plan(raw, cfg.max_queries), 1)
            return plan
        except (JsonContractError, RuntimeError) as exc:
            logger.warning("External search plan failed, searching the question as is: %s", exc)
            return None

    async def _search_all(self, indexes: IndexSet, bundle: ExternalBundle,
                          per_query: int) -> list[IndexHit]:
        async def one(index: ScholarlyIndex, query: str) -> list[IndexHit]:
            try:
                return await asyncio.to_thread(index.search, query, per_query)
            except Exception as exc:  # noqa: BLE001 - 单个索引失败不影响其它索引
                logger.warning("External search failed on %s for %r: %s", index.name, query, exc)
                bundle.errors[index.name] = str(exc)
                return []

        pairs = [(i, q) for q in bundle.queries for i in indexes.search]
        for index, query in pairs:
            bundle.request_urls[(index.name, query)] = index.search_url(query, per_query)
        results = await asyncio.gather(*(one(i, q) for i, q in pairs))
        return [hit for batch in results for hit in batch]

    async def _verify(self, indexes: IndexSet, records: list[SourceRecord]) -> None:
        gate = asyncio.Semaphore(_LOOKUP_CONCURRENCY)

        async def check(record: SourceRecord) -> None:
            if record.retracted:                      # 已撤稿的不必再查：反正不能引用
                return
            async with gate:
                for index in _lookup_chain(record, indexes)[:_LOOKUPS_PER_RECORD]:
                    if _confirmed(record) and record.fields.get("creators"):
                        break
                    try:
                        hit = await asyncio.to_thread(index.lookup, record.pid)
                    except Exception as exc:  # noqa: BLE001
                        logger.info("Lookup on %s failed for %s: %s", index.name,
                                    record.source_id, exc)
                        continue
                    if hit is not None:
                        record.lookup_hits.add(index.name)
                        _absorb(record, hit)
                if (record.pid.kind == PID_DOI and not _confirmed(record)
                        and indexes.resolver is not None):
                    try:
                        record.doi_resolves = await asyncio.to_thread(
                            indexes.resolver.exists, record.pid.value)
                    except Exception as exc:  # noqa: BLE001 - 查不了就不据此降级
                        logger.info("doi.org check failed for %s: %s", record.pid.value, exc)

        await asyncio.gather(*(check(r) for r in records))

    async def _select(self, question: str, gaps: list[str], citable: list[SourceRecord],
                      max_sources: int) -> list[tuple[str, str]] | None:
        if self._llm is None or not citable:
            return None
        candidates = [Candidate(r.source_id, str(r.fields.get("title") or ""),
                                str(r.fields.get("year") or ""), str(r.fields.get("venue") or ""),
                                str(r.fields.get("abstract") or "")) for r in citable]
        allowed = {c.source_id for c in candidates}
        prompt = render_select_prompt(question, gaps, candidates, max_sources)
        try:
            picks, _c, _t = await llm_json_call(
                self._llm, prompt, SELECT_SYSTEM,
                lambda raw: parse_selection(raw, allowed, max_sources), 1)
        except (JsonContractError, RuntimeError) as exc:
            logger.warning("External selection failed, keeping index order: %s", exc)
            return None
        return [(p.source_id, p.reason) for p in picks]

    async def _read(self, bundle: ExternalBundle, question: str,
                    cfg: ExternalEvidenceConfig) -> list[ExternalPassage]:
        from kacore.config import EXTERNAL_DEPTH_FULLTEXT

        out: list[ExternalPassage] = []
        for sid in bundle.selected:
            record = bundle.record(sid)
            if record is None:
                continue
            passages: list[ExternalPassage] = []
            if cfg.depth == EXTERNAL_DEPTH_FULLTEXT and self._can_rerank():
                passages = await self._fulltext_passages(record, question)
            if not passages:
                abstract = str(record.fields.get("abstract") or "").strip()
                if abstract:
                    passages = [ExternalPassage(sid, abstract)]
            out.extend(passages)
        return out

    def _can_rerank(self) -> bool:
        return self._reranker is not None and not self._reranker.is_passthrough

    async def _fulltext_passages(self, record: SourceRecord,
                                 question: str) -> list[ExternalPassage]:
        url = str(record.fields.get("oa_pdf_url") or "")
        if not url:
            return []
        pdf = await asyncio.to_thread(self._fulltext.fetch, url)
        if pdf is None:
            return []
        try:
            pages = await asyncio.to_thread(page_passages, pdf.path)
        except Exception as exc:  # noqa: BLE001 - 坏 PDF 退回摘要
            logger.info("Could not read %s: %s", pdf.path, exc)
            return []
        if not pages or self._reranker is None:
            return []
        chunks = [DocumentChunk(chunk_id=f"{record.source_id}#{i}", doc_id=record.source_id,
                                ordinal=i, text=p.text, content_hash="",
                                metadata={"page_number": p.page})
                  for i, p in enumerate(pages)]
        scored = await self._reranker.rerank(question, chunks, top_n=_PASSAGES_PER_SOURCE)
        record.fields.setdefault("fulltext_sha256", pdf.sha256)
        return [ExternalPassage(record.source_id, s.chunk.text,
                                int(s.chunk.metadata["page_number"]), pdf.sha256)
                for s in scored[:_PASSAGES_PER_SOURCE]]

    # ── 写账本 ──────────────────────────────────────────────

    async def persist(
        self,
        bundle: ExternalBundle,
        *,
        conv_id: str,
        msg_idx: int,
        actor: str,
        cited: Sequence[ExternalPassage],
    ) -> None:
        earlier = await self._store.list_captures(conv_id)
        seen = {h.source_id for h in await self._store.list_capture_hits(
            [c.capture_id for c in earlier])}
        now = _now()
        capture = Capture(bundle.capture_id, conv_id, msg_idx, bundle.question, bundle.queries,
                          bundle.engines, actor, bundle.started_at)
        sources = [_stored(r, bundle, now) for r in bundle.records]
        hits = [CaptureHit(bundle.capture_id, r.source_id, h.engine, h.query, h.rank,
                           r.source_id not in seen,
                           bundle.request_urls.get((h.engine, h.query), ""))
                for r in bundle.records for h in r.hits]
        fields = [FieldOrigin(r.source_id, name, engine, _field_text(r.fields.get(name)), now)
                  for r in bundle.records for name, engine in r.field_origin.items()]
        await self._store.save_capture(capture, sources, hits, fields)
        await self._store.save_citations(bundle.capture_id, [
            Citation(bundle.capture_id, p.source_id, i, p.text, p.page, p.file_sha256)
            for i, p in enumerate(cited, start=1)
        ])


# ── 组装（供 api 层把外部证据并进回答）─────────────────────


def passage_chunk(passage: ExternalPassage, ordinal: int) -> DocumentChunk:
    """外部段落 → 合成用的 DocumentChunk（doc_id 带 ext: 前缀，不与库内文档冲突）。"""
    meta: dict[str, Any] = {"external": True}
    if passage.page is not None:
        meta["page_number"] = passage.page
    return DocumentChunk(chunk_id=f"{EXTERNAL_DOC_PREFIX}{passage.source_id}#{ordinal}",
                         doc_id=f"{EXTERNAL_DOC_PREFIX}{passage.source_id}", ordinal=ordinal,
                         text=passage.text, content_hash="", metadata=meta)


def source_entry(bundle: ExternalBundle, passage: ExternalPassage, n: int) -> dict[str, Any]:
    """外部段落 → 与库内来源同形的 source dict（引用改写只读这些书目字段）。"""
    record = bundle.record(passage.source_id)
    fields = record.fields if record is not None else {}
    creators = fields.get("creators") or fields.get("creator_names") or []
    pid = record.pid if record is not None else None
    title = str(fields.get("title") or passage.source_id)
    return {
        "n": n,
        "doc_id": f"{EXTERNAL_DOC_PREFIX}{passage.source_id}",
        "title": title,
        "citation": f"{title} ({fields.get('year') or 'n.d.'})",
        "creators": creators,
        "year": str(fields.get("year") or ""),
        "venue": str(fields.get("venue") or ""),
        "doi": pid.value if pid is not None and pid.kind == PID_DOI else "",
        "url": str(fields.get("url") or ""),
        "item_type": str(fields.get("item_type") or ""),
        "pages": [passage.page] if passage.page is not None else [],
        "text": passage.text,
        "metadata": {"page_number": passage.page} if passage.page is not None else {},
        "origin": "external",
        "source_id": passage.source_id,
        "capture_id": bundle.capture_id,
        "trust": bundle.trust.get(passage.source_id, Trust.SINGLE).value,
        "abstract_only": passage.page is None,
        "file_sha256": passage.file_sha256,
    }


# ── 小工具 ──────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _by_best_rank(records: list[SourceRecord]) -> list[SourceRecord]:
    """按最佳排名排序；同名次时被更多索引给出的在前。"""
    return sorted(records, key=lambda r: (min(h.rank for h in r.hits), -len(r.engines)))


def _confirmed(record: SourceRecord) -> bool:
    return bool((record.lookup_hits | set(record.engines)) - {record.found_by})


def _lookup_chain(record: SourceRecord, indexes: IndexSet) -> list[ScholarlyIndex]:
    """按编号种类决定去哪查：DOI → Crossref / OpenAlex / DataCite；arXiv → DataCite / arXiv；
    PMID → OpenAlex；ISBN → Open Library / Crossref。给出过它的索引不再查（自己不算第二个来源）。"""
    order = {
        PID_DOI: (ENGINE_CROSSREF, ENGINE_OPENALEX, ENGINE_DATACITE, ENGINE_SEMANTIC_SCHOLAR),
        PID_ARXIV: (ENGINE_DATACITE, ENGINE_ARXIV, ENGINE_SEMANTIC_SCHOLAR),
        PID_PMID: (ENGINE_OPENALEX, ENGINE_SEMANTIC_SCHOLAR),
        PID_ISBN: (ENGINE_OPENLIBRARY, ENGINE_CROSSREF),
    }.get(record.pid.kind, ())
    engines = set(record.engines)
    return [indexes.lookup[name] for name in order
            if name in indexes.lookup and name not in engines]


def _absorb(record: SourceRecord, hit: IndexHit) -> None:
    """按编号查到的结果只补空缺字段（字段出处记为该索引），并合并撤稿标记与参考文献。"""
    for name, value in hit.fields.items():
        if value not in (None, "", [], {}) and record.fields.get(name) in (None, "", [], {}):
            record.fields[name] = value
            record.field_origin[name] = hit.engine
    record.retracted = record.retracted or hit.retracted
    record.reference_ids.update(r for r in hit.reference_ids if r)
    record.aliases.update(p.key for p in hit.pids())


def _text_of(record: SourceRecord) -> str:
    return str(record.fields.get("abstract") or record.fields.get("oa_pdf_url") or "")


def _field_text(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value or "")


def _stored(record: SourceRecord, bundle: ExternalBundle, now: str) -> StoredSource:
    meta = {k: v for k, v in record.fields.items()}
    if record.source_id in bundle.reasons:
        meta["selection_reason"] = bundle.reasons[record.source_id]
    return StoredSource(
        source_id=record.source_id,
        trust=bundle.trust.get(record.source_id, trust_level(record)).value,
        aliases=sorted(record.aliases), meta=meta, reference_ids=sorted(record.reference_ids),
        first_seen=now, checked_at=now,
    )


def fulltext_cache_dir(data_dir: Path) -> Path:
    return data_dir / "cache" / "external"


__all__ = [
    "EXTERNAL_DOC_PREFIX",
    "ExternalBundle",
    "ExternalEvidence",
    "ExternalEvidenceUnavailable",
    "ExternalPassage",
    "IndexSet",
    "fulltext_cache_dir",
    "passage_chunk",
    "source_entry",
]
