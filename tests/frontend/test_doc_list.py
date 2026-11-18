"""前端 docList.ts 的契约测试（node 转译后直接调用，无 node 则跳过）。"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def call(fn: str, *args: Any) -> Any:
    """转译 lib/docList.ts，调用导出函数 `fn(*args)`，返回 JSON 结果。"""
    if shutil.which("node") is None:
        pytest.skip("node is not available")
    if not (ROOT / "web/frontend/node_modules/typescript").exists():
        pytest.skip("frontend TypeScript dependency is not installed")

    script = textwrap.dedent(
        r"""
        const fs = require("node:fs");
        const path = require("node:path");
        const ts = require(path.join(process.cwd(), "web/frontend/node_modules/typescript"));
        const sourcePath = path.join(process.cwd(), "web/frontend/lib/docList.ts");
        const source = fs.readFileSync(sourcePath, "utf8");
        const output = ts.transpileModule(source, {
          compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
        }).outputText;
        const module = { exports: {} };
        const exports = module.exports;
        eval(output);
        const fn = module.exports[process.argv[1]];
        const args = JSON.parse(process.argv[2]);
        process.stdout.write(JSON.stringify(fn(...args)));
        """
    )
    result = subprocess.run(
        ["node", "-e", script, fn, json.dumps(list(args))],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def doc(doc_id: str, title: str = "", **kw: Any) -> dict[str, Any]:
    return {"doc_id": doc_id, "title": title or doc_id, "collection": "c", "tags": [], **kw}


def meta(creators: list[str] | None = None, year: str | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {}
    if creators is not None:
        m["creators"] = creators
    if year is not None:
        m["year"] = year
    return {"zotero_meta": m}


def ids(docs: list[dict[str, Any]]) -> list[str]:
    return [d["doc_id"] for d in docs]


def test_byline_joins_authors_and_year_and_is_null_without_both() -> None:
    assert call("bylineOf", doc("a", **meta(["Hui"], "2021"))) == "Hui · 2021"
    assert call("bylineOf", doc("a", **meta(["Hilgers", "Mangez"], "2015"))) == (
        "Hilgers, Mangez · 2015"
    )
    assert call("bylineOf", doc("a", **meta(["Peng", "Hou", "Zhou"], "2026"))) == (
        "Peng et al. · 2026"
    )
    assert call("bylineOf", doc("a", **meta(None, "1887"))) == "1887"
    assert call("bylineOf", doc("a")) is None
    assert call("bylineOf", doc("a", **meta([], ""))) is None


def test_filter_matches_title_author_year_tag_case_insensitively() -> None:
    docs = [
        doc("a", "Digital Roots", **meta(["Balbi"], "2021")),
        doc("b", "Mastering uncertainty", **meta(["Deterding"], "2022")),
        doc("c", "Capital Volume III", tags=["marx"]),
    ]
    assert ids(call("filterDocs", docs, "ROOTS")) == ["a"]
    assert ids(call("filterDocs", docs, "deterd")) == ["b"]
    assert ids(call("filterDocs", docs, "2021")) == ["a"]
    assert ids(call("filterDocs", docs, "Marx")) == ["c"]
    assert ids(call("filterDocs", docs, "   ")) == ["a", "b", "c"]


def test_sort_by_title_numeric_year_author_and_added() -> None:
    docs = [
        doc("ch10", "Chapter 10", **meta(["Zed"], "2010")),
        doc("ch2", "Chapter 2", **meta(None, "2020")),
        doc("x", "Alpha", **meta(["adams"])),
    ]
    assert ids(call("sortDocs", docs, "title")) == ["x", "ch2", "ch10"]
    # 新的在前，缺年份的在最后
    assert ids(call("sortDocs", docs, "year")) == ["ch2", "ch10", "x"]
    # 按第一作者，缺作者的在最后
    assert ids(call("sortDocs", docs, "author")) == ["x", "ch10", "ch2"]
    # 后端按加入时间升序返回；「加入时间」新的在前
    assert ids(call("sortDocs", docs, "added")) == ["x", "ch2", "ch10"]


def test_issue_only_for_problem_documents() -> None:
    assert call("docIssue", doc("a", chunks=12, milvus_covered=True)) is None
    assert call("docIssue", doc("a", chunks=0, milvus_covered=False)) == "no_text"
    assert call("docIssue", doc("a", chunks=5, needs_reindex=True)) == "pending_index"
    assert call("docIssue", doc("a", chunks=5, milvus_covered=False)) == "pending_index"
    # 字段缺失（旧后端）不当成问题
    assert call("docIssue", doc("a")) is None


def test_group_by_subcollection_follows_tree_order_and_places_each_doc_once() -> None:
    cols = [
        {"name": "Year1", "coll_key": "R"},
        {"name": "Theories", "coll_key": "T", "parent_key": "R"},
        {"name": "SNA", "coll_key": "S", "parent_key": "R"},
        {"name": "Handbook", "coll_key": "H", "parent_key": "S"},
        {"name": "Other", "coll_key": "O"},
    ]
    docs = [
        doc("t1", collection_keys=["T"]),
        doc("h1", collection_keys=["H"]),
        # 两个子集合都有：落在树序靠前的 H（SNA 在 Theories 前）
        doc("both", collection_keys=["T", "H"]),
        doc("r1", collection_keys=["R"]),
        doc("stale", collection_keys=["O"]),  # 归属不在这棵子树：放进本级
    ]
    groups = call("groupBySubcollection", docs, "R", cols)
    assert [(g["key"], g["path"], ids(g["docs"])) for g in groups] == [
        ("R", [], ["r1", "stale"]),
        ("H", ["SNA", "Handbook"], ["h1", "both"]),
        ("T", ["Theories"], ["t1"]),
    ]
    assert sum(len(g["docs"]) for g in groups) == len(docs)
