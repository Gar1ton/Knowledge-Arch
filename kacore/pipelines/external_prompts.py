"""联网补充的模型提示词与输出解析（pipelines 层）。

两次模型调用，都用「模型分配」里的 research 角色（用户自己配置的模型；
用户要求不依赖外部服务的模型）：
1. plan：读问题与库内证据摘要，指出库里缺什么，为缺的部分写学术检索词；
2. select：从已核验、可引用的候选里挑出最能支撑回答的几篇（只看标题、年份、来源、摘要）。
模型从不判断一篇文献真不真——那由 `kacore.domain.web_sources.trust_level` 按索引数据决定。
输出一律 JSON；解析不合格抛 JsonContractError，由 `llm_json_call` 重试。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from kacore.pipelines.deep_thinking_prompts import JsonContractError, extract_json_object

PLAN_SYSTEM = (
    "You plan literature searches for a research assistant. You never answer the question "
    "yourself. Output one JSON object and nothing else."
)
SELECT_SYSTEM = (
    "You choose which scholarly papers can support an answer. Judge relevance only from the "
    "given metadata. Output one JSON object and nothing else."
)
_EXCERPT_CHARS = 300
_ABSTRACT_CHARS = 700


@dataclass(frozen=True)
class SearchPlan:
    """gaps：库内证据没覆盖的方面；queries：为这些方面写的检索词（去重、截断到上限）。"""

    gaps: tuple[str, ...]
    queries: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    """被选中的候选（按模型给的顺序）与一句理由。"""

    source_id: str
    reason: str


def render_plan_prompt(question: str, local_excerpts: Sequence[tuple[str, str]],
                       max_queries: int) -> str:
    """local_excerpts：(来源标题, 片段) 列表，只取前若干字，让模型知道库里已有什么。"""
    lines = [f"- {title}: {' '.join(text.split())[:_EXCERPT_CHARS]}"
             for title, text in local_excerpts]
    local = "\n".join(lines) if lines else "(the local library returned no evidence)"
    return (
        f"Question:\n{question}\n\n"
        f"Evidence already found in the user's local library:\n{local}\n\n"
        "Task:\n"
        "1. List the aspects of the question that the local evidence does not cover (gaps).\n"
        f"2. Write at most {max_queries} search queries for scholarly indexes (Crossref, arXiv, "
        "OpenAlex) that target those gaps. Each query is an English keyword phrase of 3 to 8 "
        "words, without boolean operators or quotes. Keep names and technical terms exactly.\n"
        "3. If the local evidence already covers everything, write queries for the most useful "
        "complementary angle (recent work, counter-evidence or a key primary source).\n\n"
        'Output: {"gaps": ["..."], "queries": ["..."]}'
    )


def parse_plan(raw: str, max_queries: int) -> SearchPlan:
    data = extract_json_object(raw)
    gaps = _str_list(data.get("gaps"), "gaps")
    queries = _str_list(data.get("queries"), "queries")
    if not queries:
        raise JsonContractError("plan has no queries")
    unique = list(dict.fromkeys(" ".join(q.split()) for q in queries if q.strip()))
    return SearchPlan(tuple(g.strip() for g in gaps if g.strip()), tuple(unique[:max_queries]))


@dataclass(frozen=True)
class Candidate:
    source_id: str
    title: str
    year: str
    venue: str
    abstract: str


def render_select_prompt(question: str, gaps: Sequence[str], candidates: Sequence[Candidate],
                         max_sources: int) -> str:
    rows = [
        json.dumps({"id": c.source_id, "title": c.title, "year": c.year, "venue": c.venue,
                    "abstract": " ".join(c.abstract.split())[:_ABSTRACT_CHARS]},
                   ensure_ascii=False)
        for c in candidates
    ]
    gap_text = "\n".join(f"- {g}" for g in gaps) or "- (no specific gap given)"
    return (
        f"Question:\n{question}\n\nGaps in the local evidence:\n{gap_text}\n\n"
        "Candidate papers (one JSON object per line):\n" + "\n".join(rows) + "\n\n"
        f"Choose at most {max_sources} papers that can directly support an answer to the "
        "question, most useful first. Skip papers that are only loosely related. "
        "If two candidates are versions of the same work (for example a preprint and its "
        "published version), choose only one, preferring the published version. "
        "Use only ids from the list.\n"
        'Output: {"selected": [{"id": "...", "reason": "one short sentence"}]}'
    )


def parse_selection(raw: str, allowed_ids: set[str], max_sources: int) -> list[Selection]:
    data = extract_json_object(raw)
    items = data.get("selected")
    if not isinstance(items, list):
        raise JsonContractError("selected must be a list")
    out: list[Selection] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if sid in allowed_ids and sid not in seen:          # 模型写错的 id 丢弃，不猜
            seen.add(sid)
            out.append(Selection(sid, " ".join(str(item.get("reason") or "").split())))
    return out[:max_sources]


def _str_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise JsonContractError(f"{name} must be a list")
    return [str(v) for v in value if isinstance(v, (str, int, float))]


__all__ = [
    "PLAN_SYSTEM",
    "SELECT_SYSTEM",
    "Candidate",
    "SearchPlan",
    "Selection",
    "parse_plan",
    "parse_selection",
    "render_plan_prompt",
    "render_select_prompt",
]
