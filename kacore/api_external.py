"""回答的「来源」栏：一次联网补充的详情、按用户集合树归类、导出（api 门面 mixin）。

数据全部来自出处账本（迁移 029）：这次查到了哪些外部文献、核验结果、每家给的名次、同一对话里的
排名变化、回答实际引用的段落、字段来源；引用图谱的边只来自参考文献列表（`domain/source_graph`）。
归类（「体系」视图）在来源栏第一次打开时才调用用户配置的 research 模型，结果存回账本，之后不再调用。
导出列与 ai-source-scraper 1.7.0 用户脚本的 15 列一致，后面追加 KA 的核验与引用列。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from kacore.domain.source_graph import GraphSource, build_edges
from kacore.domain.web_sources import rank_drift

if TYPE_CHECKING:
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("KnowledgeRepositoryApi")

# 归类提示里每个集合最多列出的已有论文标题数（控制提示长度的假设值）。
PLACEMENT_TITLES_PER_COLLECTION = 12
SCRAPER_COLUMNS = (
    "rank", "capture_id", "capture_timestamp", "platform", "page_url", "page_title",
    "session_label", "source_type", "url", "clean_url", "hostname", "domain", "title",
    "description", "favicon",
)
KA_COLUMNS = ("trust", "pid", "cited_passages", "pages")


class ExternalApiMixin:
    """组合进 KnowledgeRepositoryApi。依赖：_source_store、_external_evidence、_library_dois。"""

    _source_store: SourceDocumentStore
    _external_evidence: Any

    async def _library_dois(self) -> dict[str, str]:  # 由 KnowledgeRepositoryApi 实现
        raise NotImplementedError

    # ── 详情 ────────────────────────────────────────────────

    async def get_external_capture(self, capture_id: str) -> dict[str, Any]:
        store = self._source_store
        capture = await store.get_capture(capture_id)
        if capture is None:
            raise ValueError(f"capture not found: {capture_id}")
        conversation = await store.list_captures(capture.conv_id)
        order = [c.capture_id for c in conversation]
        hits = await store.list_capture_hits(order)
        citations = await store.list_citations(capture_id)
        mine = [h for h in hits if h.capture_id == capture_id]
        best: dict[str, int] = {}
        for h in mine:
            best[h.source_id] = min(h.rank, best.get(h.source_id, h.rank))
        source_ids = sorted(best, key=lambda sid: best[sid])
        stored = await store.get_sources(source_ids)
        ranks: dict[str, dict[str, int]] = {}
        for h in hits:
            per = ranks.setdefault(h.source_id, {})
            per[h.capture_id] = min(h.rank, per.get(h.capture_id, h.rank))
        cited: dict[str, list[dict[str, Any]]] = {}
        for c in citations:
            cited.setdefault(c.source_id, []).append(
                {"ord": c.ord, "passage": c.passage, "page": c.page, "file_sha256": c.file_sha256})
        sources = []
        for sid in source_ids:
            s = stored.get(sid)
            if s is None:
                continue
            drift = rank_drift(ranks.get(sid, {}), order[: order.index(capture_id) + 1])
            sources.append({
                "source_id": sid, "trust": s.trust, "meta": s.meta, "aliases": s.aliases,
                "placement": s.placement, "best_rank": best[sid],
                "hits": [{"engine": h.engine, "query": h.query, "rank": h.rank, "is_new": h.is_new,
                          "request_url": h.request_url} for h in mine if h.source_id == sid],
                "history": [{"capture_id": cid, "rank": r}
                            for cid, r in ranks.get(sid, {}).items()],
                "drift": list(drift) if drift else None,
                "cited": cited.get(sid, []),
                "fields": [{"field": f.field, "engine": f.engine, "fetched_at": f.fetched_at}
                           for f in await store.list_source_fields(sid)],
            })
        library = await self._library_dois()
        edges = build_edges(
            [GraphSource(x["source_id"], frozenset(x["aliases"]),
                         frozenset(stored[x["source_id"]].reference_ids)) for x in sources],
            library,
        )
        return {
            "capture": {"capture_id": capture.capture_id, "conversation_id": capture.conv_id,
                        "msg_idx": capture.msg_idx, "question": capture.question,
                        "queries": capture.queries, "engines": capture.engines,
                        "actor": capture.actor, "captured_at": capture.captured_at,
                        "ordinal": order.index(capture_id) + 1},
            "sources": sources,
            "graph": {"edges": [e.__dict__ for e in edges],
                      "library_nodes": sorted({e.target for e in edges
                                               if e.kind == "cites_library"})},
        }

    # ── 归类（体系）─────────────────────────────────────────

    async def place_external_capture(self, capture_id: str) -> dict[str, Any]:
        """按用户自己的集合树归类这次的外部文献（已归类的不重复调用模型）。"""
        from kacore.pipelines.external_prompts import Candidate

        detail = await self.get_external_capture(capture_id)
        todo = [s for s in detail["sources"]
                if not s["placement"] and s["trust"] in ("verified", "single")]
        if todo and self._external_evidence is not None:
            outline = await self._collection_outline()
            papers = [Candidate(s["source_id"], str(s["meta"].get("title") or ""),
                                str(s["meta"].get("year") or ""),
                                str(s["meta"].get("venue") or ""),
                                str(s["meta"].get("abstract") or "")) for s in todo]
            placed = await self._external_evidence.place(papers, outline)
            if placed:
                await self._source_store.update_placements({
                    p.source_id: {"path": p.path, "new_direction": p.new_direction,
                                  "reason": p.reason} for p in placed})
                detail = await self.get_external_capture(capture_id)
        return detail

    async def _collection_outline(self) -> list[tuple[str, list[str]]]:
        """(集合路径, 前若干篇论文标题)；路径用「 › 」连接父子集合名。"""
        collections = await self._source_store.list_collections()
        by_key = {c.coll_key: c for c in collections}

        def path_of(key: str) -> str:
            names, seen = [], set()
            while key and key in by_key and key not in seen:
                seen.add(key)
                names.append(by_key[key].name)
                key = by_key[key].parent_key
            return " › ".join(reversed(names))

        titles: dict[str, list[str]] = {}
        for doc in await self._source_store.list_documents():
            for key in doc.collection_keys:
                bucket = titles.setdefault(key, [])
                if len(bucket) < PLACEMENT_TITLES_PER_COLLECTION:
                    bucket.append(doc.title)
        return [(path_of(c.coll_key), titles.get(c.coll_key, [])) for c in collections]

    # ── 导出 ────────────────────────────────────────────────

    async def export_external_capture(self, capture_id: str, folder: str) -> dict[str, Any]:
        """写 `ka-sources_<capture>.csv` 与 `.json` 到用户选的文件夹；返回写出的路径。"""
        target = Path(folder).expanduser()
        if not target.is_dir():
            raise ValueError(f"folder does not exist: {folder}")
        detail = await self.get_external_capture(capture_id)
        rows = scraper_rows(detail)
        stem = target / f"ka-sources_{capture_id}"
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=[*SCRAPER_COLUMNS, *KA_COLUMNS],
                                quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
        csv_path, json_path = stem.with_suffix(".csv"), stem.with_suffix(".json")
        csv_path.write_text(buffer.getvalue(), encoding="utf-8")
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"files": [str(csv_path), str(json_path)], "rows": len(rows)}


def scraper_rows(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """来源栏详情 → 导出行（每篇外部文献一行；source_type = cited / retrieved）。"""
    cap = detail["capture"]
    rows = []
    for s in detail["sources"]:
        meta = s["meta"]
        url = str(meta.get("url") or "")
        host = urlsplit(url).hostname or ""
        rows.append({
            "rank": s["best_rank"], "capture_id": cap["capture_id"],
            "capture_timestamp": cap["captured_at"], "platform": "knowledge-arch",
            "page_url": cap["conversation_id"], "page_title": cap["question"],
            "session_label": "", "source_type": "cited" if s["cited"] else "retrieved",
            "url": url, "clean_url": url, "hostname": host,
            "domain": ".".join(host.split(".")[-2:]) if host else "",
            "title": meta.get("title", ""), "description": meta.get("abstract", ""),
            "favicon": "", "trust": s["trust"], "pid": s["source_id"],
            "cited_passages": " | ".join(c["passage"] for c in s["cited"]),
            "pages": ";".join(str(c["page"]) for c in s["cited"] if c["page"] is not None),
        })
    return rows


__all__ = ["ExternalApiMixin", "KA_COLUMNS", "SCRAPER_COLUMNS", "scraper_rows"]
