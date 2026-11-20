"""外部文献的引用图谱（domain 层·纯函数，零依赖）。

边只来自参考文献列表（书目数据，不是模型输出，模型不连线）：
- cites：A 的参考文献里有 B 的任一标识（A 引用 B）；
- coupling：A 与 B 共同引用了几篇（文献耦合，权重 = 共同参考文献数）；
- 库内节点：A 的参考文献里有某篇库内文档的 DOI，则连到这篇库内文档（库内全文是更可靠的证据）。
参考文献标识与文献标识都已是 canonical 形态（`doi:…`、`openalex:W…`），按字符串相等匹配。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations

LIBRARY_PREFIX = "lib:"


@dataclass(frozen=True)
class GraphSource:
    source_id: str
    aliases: frozenset[str]
    reference_ids: frozenset[str]


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str          # cites | coupling | cites_library
    weight: int = 1


def build_edges(sources: list[GraphSource],
                library_dois: Mapping[str, str] | None = None) -> list[Edge]:
    """sources 之间的引用与耦合边，以及指向库内文档（`lib:<doc_id>`）的引用边。"""
    owner: dict[str, str] = {}
    for s in sources:
        for alias in s.aliases | {s.source_id}:
            owner.setdefault(alias, s.source_id)
    edges: list[Edge] = []
    for s in sources:
        targets = sorted({owner[r] for r in s.reference_ids if r in owner} - {s.source_id})
        edges.extend(Edge(s.source_id, t, "cites") for t in targets)
        for ref in sorted(s.reference_ids):
            if ref.startswith("doi:") and library_dois and ref[4:] in library_dois:
                edges.append(Edge(s.source_id, LIBRARY_PREFIX + library_dois[ref[4:]],
                                  "cites_library"))
    for a, b in combinations(sources, 2):
        shared = len(a.reference_ids & b.reference_ids)
        if shared:
            edges.append(Edge(a.source_id, b.source_id, "coupling", shared))
    return edges


__all__ = ["LIBRARY_PREFIX", "Edge", "GraphSource", "build_edges"]
