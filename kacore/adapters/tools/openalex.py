"""OpenAlex 检索工具（adapters 层）：跨学科的书目数据、开放获取 PDF 与撤稿标记。

只取书目字段；摘要以倒排索引给出，这里按位置还原成原文（格式还原，不是改写）。不取 OpenAlex 的
主题分类、相关作品等模型产出（用户要求 KA 不依赖外部模型）。

额度（用户 11-20：不默认关，走免费，用完拉倒）：2026-02-13 起没有 key 时每天约 0.1 美元，
一次检索约 0.001 美元（2026-11-20 实测响应头 X-RateLimit-*-USD）。额度用完后到重置时间前
不再请求，直接告诉模型；额度只在内存里，重启后从下一次响应重新得知。
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from kacore.adapters.tools.base import (
    HttpClient,
    Opener,
    Tool,
    ToolError,
    fetch_json,
    no_results,
    query_args,
    record_block,
    search_schema,
    year_of,
)
from kacore.domain.tool_calls import SETTING_SECRET, Block, ToolResult, ToolSetting, ToolSpec

API = "https://api.openalex.org/works"
SELECT = ",".join([
    "id", "doi", "ids", "title", "publication_year", "type", "authorships", "primary_location",
    "best_oa_location", "is_retracted", "abstract_inverted_index", "cited_by_count",
])
_H_LIMIT = "x-ratelimit-limit-usd"
_H_REMAINING = "x-ratelimit-remaining-usd"
_H_COST = "x-ratelimit-cost-usd"
_H_RESET = "x-ratelimit-reset"


class OpenAlexSearch(Tool):
    spec = ToolSpec(
        name="openalex_search",
        description=(
            "Search OpenAlex, a cross-disciplinary index of scholarly works. Returns title, "
            "authors, year, venue, DOI, retraction flag, abstract and an open-access PDF link "
            "when one exists."
        ),
        input_schema=search_schema("Keywords: topic words, author names, title."),
        settings=(ToolSetting("api_key", SETTING_SECRET),),
    )

    def __init__(self, *, api_key: str = "", opener: Opener | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._key = api_key.strip()
        self._http = HttpClient("openalex", opener=opener, min_interval=0.05)
        self._clock = clock
        self._limit: float | None = None
        self._remaining: float | None = None
        self._paused_until = 0.0

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        query, limit = query_args(arguments)
        if self._clock() < self._paused_until:
            return ToolResult.error(
                f"OpenAlex free quota is used up until {_iso(self._paused_until)}; "
                "use another search tool.")
        params = {"search": query, "per-page": limit, "select": SELECT,
                  **({"api_key": self._key} if self._key else {})}
        try:
            payload = await fetch_json(self._http, API, params)
        except ToolError as exc:
            self._read_quota(exc.status)
            raise
        self._read_quota(200)
        works = payload.get("results") or []
        return ToolResult([to_block(w) for w in works]) if works else no_results(query)

    def status(self) -> dict[str, str]:
        out: dict[str, str] = {"key": "set" if self._key else "none"}
        if self._remaining is not None and self._limit is not None:
            out["quota"] = f"${self._remaining:.3f} / ${self._limit:.2f}"
        if self._clock() < self._paused_until:
            out["paused_until"] = _iso(self._paused_until)
        return out

    def _read_quota(self, status: int | None) -> None:
        headers = self._http.last_headers
        limit, remaining = _num(headers.get(_H_LIMIT)), _num(headers.get(_H_REMAINING))
        cost = _num(headers.get(_H_COST)) or 0.0
        self._limit = limit if limit is not None else self._limit
        self._remaining = remaining if remaining is not None else self._remaining
        spent = remaining is not None and remaining < max(cost, 1e-9)
        refused = status in (409, 429) and (remaining is None or remaining <= cost)
        if spent or refused:
            reset = _num(headers.get(_H_RESET))
            now = self._clock()
            self._paused_until = now + reset if reset and reset > 0 else _next_utc_midnight(now)


def abstract_from_inverted(index: dict[str, list[int]] | None) -> str:
    """abstract_inverted_index（词 → 位置列表）按位置还原成原文。"""
    if not index:
        return ""
    slots = {pos: word for word, positions in index.items() for pos in positions}
    return " ".join(slots[i] for i in sorted(slots))


def to_block(work: dict[str, Any]) -> Block:
    """一条 OpenAlex work → 一块文本（纯映射）。"""
    source = ((work.get("primary_location") or {}).get("source") or {})
    doi = str(work.get("doi") or "").removeprefix("https://doi.org/")
    return record_block([
        ("Title", work.get("title") or ""),
        ("Authors", [(a.get("author") or {}).get("display_name") or ""
                     for a in work.get("authorships") or []]),
        ("Year", year_of(work.get("publication_year"))),
        ("Venue", source.get("display_name") or ""),
        ("Type", work.get("type") or ""),
        ("DOI", doi),
        ("OpenAlex", str(work.get("id") or "").rsplit("/", 1)[-1]),
        ("Open-access PDF", (work.get("best_oa_location") or {}).get("pdf_url") or ""),
        ("Cited by", work.get("cited_by_count") or ""),
        ("Retracted", "yes" if work.get("is_retracted") else ""),
        ("Abstract", abstract_from_inverted(work.get("abstract_inverted_index"))),
    ])


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="minutes")


def _next_utc_midnight(now: float) -> float:
    day = datetime.fromtimestamp(now, timezone.utc).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()


__all__ = ["OpenAlexSearch", "abstract_from_inverted", "to_block"]
