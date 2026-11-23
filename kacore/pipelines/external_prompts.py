"""联网补充的模型提示词与输出解析（pipelines 层）。

都用「模型分配」里的 research 角色（用户自己配置的模型；用户要求不依赖外部服务的模型）：
1. distributor：读问题与库内证据，按任务内容挑工具调用，用 `cite` 登记要引用的外部来源；
   它不写回答（回答由既有合成写），也不判断文献真伪（可追溯核验由 `trace_check` 逐字核对）；
2. 归类：把引用按用户自己的集合树归类（来源栏「体系」视图首次打开时）。
这里还放交给模型的工具结果与登记结果的措辞。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from kacore.domain.tool_calls import CITE_TOOL, TraceCheck
from kacore.pipelines.deep_thinking_prompts import JsonContractError, extract_json_object

_EXCERPT_CHARS = 300
_ABSTRACT_CHARS = 700

# ── distributor ─────────────────────────────────────────────

DISTRIBUTOR_SYSTEM = (
    "You gather external evidence for a research assistant. You never write the answer. "
    "Use the tools to find what the user's own library lacks, read the most useful sources, "
    f"then register each source the answer should cite with `{CITE_TOOL}`, copying the passage "
    "and bibliographic fields word for word from tool results. Every tool result starts with "
    f"its call id (c1, c2, ...); give that id as `call` in `{CITE_TOOL}`. If a registration is "
    "rejected, fix it from the tool results or drop it. Register only sources that directly "
    "support the answer, at most a handful. Stop calling tools when you are done."
)


def render_task(question: str, local_excerpts: Sequence[tuple[str, str]], rounds: int) -> str:
    """local_excerpts：(来源标题, 片段) 列表，只取前若干字，让模型知道库里已有什么。
    rounds：本次最多几轮。真实模型冒烟（11-22）里模型不知道轮数，四轮都在检索、一条也没登记。"""
    lines = [f"- {title}: {' '.join(text.split())[:_EXCERPT_CHARS]}"
             for title, text in local_excerpts]
    local = "\n".join(lines) if lines else "(the local library returned no evidence)"
    budget = (
        f"You have {rounds} rounds of tool calls (calls in one round run in parallel). "
        f"Register sources with `{CITE_TOOL}` by round {rounds - 1}; the last round offers only "
        f"`{CITE_TOOL}`, to fix rejected registrations."
        if rounds > 1 else "You have one round of tool calls."
    )
    return (
        f"Question:\n{question}\n\n"
        f"Evidence already found in the user's local library:\n{local}\n\n"
        "Find external sources for what this evidence does not cover (or, if it covers "
        "everything, the most useful complementary angle: recent work, counter-evidence or a key "
        "primary source). Search queries work best as English keyword phrases of 3 to 8 words.\n\n"
        + budget
    )


# 最后一轮之前追加的提示：只剩 cite，没登记的来源现在登记。
FINAL_ROUND_NOTE = (
    f"Searching is over: this is the last round and only `{CITE_TOOL}` is available. Register "
    "the sources the answer should cite now, copying from the tool results above. If none "
    "support the answer, reply without calling a tool."
)


def render_refused(tool: str) -> str:
    """模型调用了本轮没开放的工具（最后一轮只开放 cite）时交回的文本。"""
    return f"refused: `{tool}` is not available in this round; only `{CITE_TOOL}` is."


def render_call_result(label: str, tool: str, body: str) -> str:
    """一次工具调用交给模型的文本：先给调用编号，模型在 cite 里用它指明出处。"""
    return f"call {label} · {tool}\n{body}"


def render_cite_result(check: TraceCheck, *, ord_: int | None, label: str,
                       duplicate_of: int | None = None) -> str:
    if duplicate_of is not None:
        return f"already registered as E{duplicate_of}"
    if check.ok and ord_ is not None:
        return f"accepted as E{ord_} (call {label}, block {check.block + 1})"
    if "call" in check.missing:
        return "rejected: `call` must be the id of an earlier tool call that returned results"
    if "passage" in check.missing:
        return f"rejected: the passage does not appear word for word in call {label}"
    return ("rejected: these fields do not appear word for word in any tool result: "
            + ", ".join(check.missing) + ". Copy them exactly or leave them out.")


@dataclass(frozen=True)
class Candidate:
    """归类用的一条引用：source_id 是来源栏编号（E1、E2…）。"""

    source_id: str
    title: str
    year: str
    venue: str
    abstract: str


# ── 归类：按用户自己的集合树（「体系」视图；在来源栏首次打开时才调用）──────────

PLACE_SYSTEM = (
    "You file papers into a researcher's own collection tree. Use only the collection paths "
    "given. Output one JSON object and nothing else."
)


@dataclass(frozen=True)
class Placement:
    """path 非空 = 放进这个集合；否则 new_direction 是模型起的「新方向」短名。"""

    source_id: str
    path: str
    new_direction: str
    reason: str


def render_placement_prompt(outline: Sequence[tuple[str, Sequence[str]]],
                            papers: Sequence[Candidate]) -> str:
    """outline：(集合路径, 集合里已有论文的标题) 列表；papers：要归类的外部文献。"""
    tree = "\n".join(
        f"- {path}: " + "; ".join(t for t in titles) if titles else f"- {path}: (empty)"
        for path, titles in outline
    )
    rows = "\n".join(
        json.dumps({"id": c.source_id, "title": c.title, "year": c.year,
                    "abstract": " ".join(c.abstract.split())[:_ABSTRACT_CHARS]},
                   ensure_ascii=False)
        for c in papers
    )
    return (
        "The researcher's collections (path: titles of papers already in it):\n"
        f"{tree}\n\nPapers to file (one JSON object per line):\n{rows}\n\n"
        "For each paper choose the single collection path it fits best, judged by the topic of "
        "the papers already there. If no collection fits, leave path empty and give a short name "
        "(2-5 words, in the language of the collection names) for the new direction it opens. "
        "Give one short sentence of reason.\n"
        'Output: {"items": [{"id": "...", "path": "...", "new_direction": "", '
        '"reason": "..."}]}'
    )


def parse_placements(raw: str, allowed_ids: set[str], paths: set[str]) -> list[Placement]:
    data = extract_json_object(raw)
    items = data.get("items")
    if not isinstance(items, list):
        raise JsonContractError("items must be a list")
    out: list[Placement] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        path = str(item.get("path") or "").strip()
        new = " ".join(str(item.get("new_direction") or "").split())
        if sid not in allowed_ids:
            continue
        if path and path not in paths:                      # 写错的路径不收，不猜
            path = ""
        if not path and not new:
            continue
        out.append(Placement(sid, path, "" if path else new,
                             " ".join(str(item.get("reason") or "").split())))
    return out


__all__ = [
    "DISTRIBUTOR_SYSTEM",
    "FINAL_ROUND_NOTE",
    "PLACE_SYSTEM",
    "Candidate",
    "Placement",
    "parse_placements",
    "render_call_result",
    "render_cite_result",
    "render_placement_prompt",
    "render_refused",
    "render_task",
]
