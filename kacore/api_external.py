"""联网补充的 api 门面 mixin：来源栏、工具面板与 AI 助手（skill）的工具调用。

数据全部来自调用账本（迁移 029_tool_calls）：一次运行里调用了哪些工具、模型看到的原文、登记并通过
可追溯核验的引用、回答是否用到。「体系」视图在来源栏首次打开时才调用研究模型归类，结果存回账本。
导出列与 ai-source-scraper 1.7.0 用户脚本的 15 列一致，后面追加工具与调用列。

skill 路径（用户 11-22 选「助手自己挑」）：助手列出工具、调用、登记引用，KA 只执行、核对、记账，
不调用 KA 的模型。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from kacore.domain.models import DocumentChunk
from kacore.domain.paper_import import doi_key
from kacore.domain.tool_calls import ACTOR_AGENT, ACTOR_TEST, CITE_TOOL, Citation, ToolCall

if TYPE_CHECKING:
    from kacore.managers.tool_distributor import ToolDistributor, ToolSession
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("KnowledgeRepositoryApi")

EXTERNAL_DOC_PREFIX = "ext:"
# 归类提示里每个集合最多列出的已有论文标题数（控制提示长度的假设值）。
PLACEMENT_TITLES_PER_COLLECTION = 12
RECENT_CALLS = 50
SCRAPER_COLUMNS = (
    "rank", "capture_id", "capture_timestamp", "platform", "page_url", "page_title",
    "session_label", "source_type", "url", "clean_url", "hostname", "domain", "title",
    "description", "favicon",
)
KA_COLUMNS = ("tool", "call_id", "block", "locator", "passage", "cited")


def source_id_of(citation: Citation) -> str:
    """来源栏编号 E1、E2…（回答里外部来源的 source_id，前端据此定位）。"""
    return f"E{citation.ord}"


def external_chunk(citation: Citation) -> DocumentChunk:
    """外部引用 → 合成用的 DocumentChunk（doc_id 带 ext: 前缀，不与库内文档冲突）。"""
    doc_id = f"{EXTERNAL_DOC_PREFIX}{citation.capture_id}:{citation.ord}"
    meta: dict[str, Any] = {"external": True}
    if citation.page is not None:
        meta["page_number"] = citation.page
    return DocumentChunk(chunk_id=doc_id, doc_id=doc_id, ordinal=citation.ord,
                         text=citation.passage, content_hash="", metadata=meta)


def external_source_entry(session: ToolSession, citation: Citation, n: int) -> dict[str, Any]:
    """外部引用 → 与库内来源同形的 source dict（引用改写只读这些书目字段）。"""
    fields = citation.fields
    title = str(fields.get("title") or source_id_of(citation))
    call = next((c for c in session.calls.values() if c.call_id == citation.call_id), None)
    identifier = str(fields.get("identifier") or "")
    return {
        "n": n,
        "doc_id": f"{EXTERNAL_DOC_PREFIX}{citation.capture_id}:{citation.ord}",
        "title": title,
        "citation": f"{title} ({fields.get('year') or 'n.d.'})",
        "creators": list(fields.get("creators") or []),
        "year": str(fields.get("year") or ""),
        "venue": str(fields.get("venue") or ""),
        "doi": doi_key(identifier),
        "url": str(fields.get("url") or ""),
        "item_type": "",
        "pages": [citation.page] if citation.page is not None else [],
        "text": citation.passage,
        "metadata": {"page_number": citation.page} if citation.page is not None else {},
        "origin": "external",
        "source_id": source_id_of(citation),
        "ord": citation.ord,
        "capture_id": citation.capture_id,
        "tool": call.tool if call else "",
        "locator": citation.locator,
    }


def _call_json(call: ToolCall, label: str) -> dict[str, Any]:
    return {"call_id": call.call_id, "label": label, "ord": call.ord, "tool": call.tool,
            "arguments": call.arguments, "is_error": call.is_error,
            "started_at": call.started_at, "elapsed_ms": call.elapsed_ms,
            "blocks": [{"text": b.text, "locator": b.locator, "page": b.page}
                       for b in call.blocks]}


class ExternalApiMixin:
    """组合进 KnowledgeRepositoryApi。依赖：_source_store、_tool_distributor。"""

    _source_store: SourceDocumentStore
    _tool_distributor: ToolDistributor | None

    # ── 来源栏 ──────────────────────────────────────────────

    async def get_external_capture(self, capture_id: str) -> dict[str, Any]:
        store = self._source_store
        capture = await store.get_capture(capture_id)
        if capture is None:
            raise ValueError(f"capture not found: {capture_id}")
        calls = await store.list_tool_calls(capture_id)
        by_id = {c.call_id: c for c in calls}
        imports = await self.capture_imports(capture_id)  # type: ignore[attr-defined]
        sources = []
        for citation in await store.list_citations(capture_id):
            call = by_id.get(citation.call_id)
            sources.append({
                "source_id": source_id_of(citation), "ord": citation.ord,
                "fields": citation.fields, "passage": citation.passage,
                "locator": citation.locator, "page": citation.page, "cited": citation.cited,
                "placement": citation.placement, "call_id": citation.call_id,
                "call_label": f"c{call.ord}" if call else "", "tool": call.tool if call else "",
                "block": citation.block, "import": imports.get(citation.ord),
            })
        return {
            "capture": {"capture_id": capture.capture_id, "conversation_id": capture.conv_id,
                        "msg_idx": capture.msg_idx, "question": capture.question,
                        "actor": capture.actor, "captured_at": capture.captured_at},
            "sources": sources,
            "calls": [_call_json(c, f"c{c.ord}") for c in calls],
        }

    async def place_external_capture(self, capture_id: str) -> dict[str, Any]:
        """按用户自己的集合树归类这次的引用（已归类的不重复调用模型）。"""
        from kacore.pipelines.external_prompts import Candidate

        detail = await self.get_external_capture(capture_id)
        todo = [s for s in detail["sources"] if not s["placement"]]
        if todo and self._tool_distributor is not None:
            outline = await self._collection_outline()
            papers = [Candidate(s["source_id"], str(s["fields"].get("title") or ""),
                                str(s["fields"].get("year") or ""),
                                str(s["fields"].get("venue") or ""), s["passage"])
                      for s in todo]
            placed = await self._tool_distributor.place(papers, outline)
            if placed:
                ords = {s["source_id"]: s["ord"] for s in todo}
                await self._source_store.update_placements(capture_id, {
                    ords[p.source_id]: {"path": p.path, "new_direction": p.new_direction,
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

    async def export_external_capture(self, capture_id: str, folder: str) -> dict[str, Any]:
        """写 `ka-sources_<capture>.csv` 与 `.json` 到用户选的文件夹；返回写出的路径。"""
        target = Path(folder).expanduser()
        if not target.is_dir():
            raise ValueError(f"folder does not exist: {folder}")
        rows = scraper_rows(await self.get_external_capture(capture_id))
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

    # ── 工具面板与 AI 助手 ──────────────────────────────────

    def _toolbox(self) -> Any:
        if self._tool_distributor is None:
            raise RuntimeError("external tools are not configured")
        return self._tool_distributor.toolbox

    def list_tools(self) -> list[dict[str, Any]]:
        """工具面板与助手用：每个工具的声明、开关、是否就绪、缺哪些设置、最近成功 / 错误。"""
        return self._toolbox().status()

    async def recent_tool_calls(self, limit: int = RECENT_CALLS) -> list[dict[str, Any]]:
        out = []
        for call, capture in await self._source_store.list_recent_tool_calls(limit):
            out.append({**_call_json(call, f"c{call.ord}"), "capture_id": capture.capture_id,
                        "actor": capture.actor, "question": capture.question,
                        "conversation_id": capture.conv_id})
        return out

    async def call_tool(self, name: str, arguments: dict[str, Any], *, capture_id: str = "",
                        question: str = "", conversation_id: str = "",
                        actor: str = ACTOR_AGENT) -> dict[str, Any]:
        """调用一个工具并记账。capture_id 为空时新建一次运行（助手的一组调用共用一个 capture_id，
        cite 时据此找到这些调用）；工具面板的试调用 actor=test。"""
        session = await self._session(capture_id, question or f"{name} call",
                                      conversation_id, actor)
        if name == CITE_TOOL:
            raise ValueError("use the cite endpoint to register a source")
        call, text = await session.call(name, arguments)
        return {"capture_id": session.capture.capture_id, "call": _call_json(call, f"c{call.ord}"),
                "text": text}

    async def cite_tool(self, capture_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """助手登记一条引用：KA 逐字核对，通过的给出可直接写进回答的 Harvard 引用。"""
        from kacore.managers.tool_distributor import ToolSession
        from kacore.pipelines.citation_rendering import annotate_sources

        session = await ToolSession.resume(self._toolbox(), self._source_store, capture_id)
        if session is None:
            raise ValueError(f"capture not found: {capture_id}")
        before = len(session.citations)
        _call, text = await session.cite(arguments)
        accepted = session.citations[before:] if len(session.citations) > before else []
        entries = [external_source_entry(session, c, i)
                   for i, c in enumerate(accepted, start=1)]
        annotate_sources(entries)
        return {"capture_id": capture_id, "accepted": bool(accepted), "message": text,
                "source": entries[0] if entries else None}

    async def _session(self, capture_id: str, question: str, conversation_id: str,
                       actor: str) -> ToolSession:
        from kacore.managers.tool_distributor import ToolSession

        toolbox, store = self._toolbox(), self._source_store
        if capture_id:
            session = await ToolSession.resume(toolbox, store, capture_id)
            if session is None:
                raise ValueError(f"capture not found: {capture_id}")
            return session
        actor = actor if actor in (ACTOR_AGENT, ACTOR_TEST) else ACTOR_AGENT
        return await ToolSession.open(toolbox, store, conv_id=conversation_id.strip() or actor,
                                      question=question, actor=actor)

    async def _library_dois(self) -> dict[str, str]:
        """库里有 DOI 的文档：DOI（规整后）→ doc_id。

        Zotero 文档取镜像条目的 DOI，本地文档取书目字段里的 DOI。
        """
        out: dict[str, str] = {}
        docs = await self._source_store.list_documents()
        by_item = {(d.library_id, d.zotero_item_key): d.doc_id for d in docs if d.zotero_item_key}
        for item in await self._source_store.list_zotero_items():
            doc_id = by_item.get((item.library_id, item.item_key))
            doi = doi_key(getattr(item, "doi", "") or "")
            if doc_id and doi:
                out.setdefault(doi, doc_id)
        for doc in docs:
            doi = doi_key(str((doc.local_meta or {}).get("doi") or ""))
            if doi:
                out.setdefault(doi, doc.doc_id)
        return out


def scraper_rows(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """来源栏详情 → 导出行（每条引用一行；source_type = cited / registered）。"""
    cap = detail["capture"]
    rows = []
    for s in detail["sources"]:
        fields = s["fields"]
        url = str(fields.get("url") or "")
        host = urlsplit(url).hostname or ""
        rows.append({
            "rank": s["ord"], "capture_id": cap["capture_id"],
            "capture_timestamp": cap["captured_at"], "platform": "knowledge-arch",
            "page_url": cap["conversation_id"], "page_title": cap["question"],
            "session_label": "", "source_type": "cited" if s["cited"] else "registered",
            "url": url, "clean_url": url, "hostname": host,
            "domain": ".".join(host.split(".")[-2:]) if host else "",
            "title": fields.get("title", ""), "description": "", "favicon": "",
            "tool": s["tool"], "call_id": s["call_label"], "block": s["block"] + 1,
            "locator": s["locator"], "passage": s["passage"], "cited": int(bool(s["cited"])),
        })
    return rows


__all__ = [
    "EXTERNAL_DOC_PREFIX",
    "KA_COLUMNS",
    "SCRAPER_COLUMNS",
    "ExternalApiMixin",
    "doi_key",
    "external_chunk",
    "external_source_entry",
    "scraper_rows",
    "source_id_of",
]
