"""引用图谱：边只来自参考文献列表（纯函数）。"""
from __future__ import annotations

from kacore.domain.source_graph import GraphSource, build_edges


def _g(sid: str, aliases: set[str], refs: set[str]) -> GraphSource:
    return GraphSource(sid, frozenset(aliases), frozenset(refs))


def test_cites_coupling_and_library_edges() -> None:
    a = _g("doi:10.1/a", {"doi:10.1/a", "openalex:W1"},
           {"openalex:W2", "doi:10.9/x", "doi:10.5/lib"})
    b = _g("doi:10.1/b", {"doi:10.1/b", "openalex:W2"}, {"doi:10.9/x", "doi:10.9/y"})
    c = _g("arxiv:2601.1", {"arxiv:2601.1"}, set())
    edges = {(e.source, e.target, e.kind, e.weight)
             for e in build_edges([a, b, c], {"10.5/lib": "doc-7"})}
    assert ("doi:10.1/a", "doi:10.1/b", "cites", 1) in edges          # A 引用 B（按别名对上）
    assert ("doi:10.1/a", "doi:10.1/b", "coupling", 1) in edges       # 共同引用 10.9/x
    assert ("doi:10.1/a", "lib:doc-7", "cites_library", 1) in edges   # 引用了库内文档
    assert not any(e[0] == "arxiv:2601.1" or e[1] == "arxiv:2601.1" for e in edges)
