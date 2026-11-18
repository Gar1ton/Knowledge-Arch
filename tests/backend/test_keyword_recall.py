"""关键词通道（SQLite FTS5 / BM25，迁移 028）：排序、词干、中文、特殊字符、索引同步、候选口径。"""
from __future__ import annotations

import os

import aiosqlite
import pytest

from kacore.config import Config
from kacore.domain.models import (
    Collection,
    DocumentChunk,
    DocumentLifecycle,
    PageChunk,
    SourceDocument,
)
from kacore.migration_runner import run_migrations
from kacore.pipelines.retrieval_orchestrator import RetrievalOrchestrator
from kacore.repository.source_store.search_index import backfill_search_index
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore


@pytest.fixture
async def store(tmp_path):
    db = await aiosqlite.connect(os.path.join(tmp_path, "kw.db"))
    await db.execute("PRAGMA foreign_keys = ON")
    await run_migrations(db)
    s = SQLiteSourceDocumentStore(db)
    await s.upsert_collection(Collection(name="papers"))
    await s.upsert_collection(Collection(name="other"))
    yield s
    await db.close()


async def _doc(store, doc_id: str, texts: list[str], *, collection: str = "papers",
               state: DocumentLifecycle = DocumentLifecycle.ACTIVE) -> None:
    doc = SourceDocument(doc_id, doc_id, f"{doc_id}.pdf", "application/pdf", 1, doc_id, collection)
    doc.lifecycle_state = state
    await store.add_document(doc)
    await store.replace_chunks(doc_id, [
        DocumentChunk(f"{doc_id}:{i}", doc_id, i, text, f"h-{doc_id}-{i}")
        for i, text in enumerate(texts)
    ])


def _orchestrator(store) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(source_store=store, config=Config({}))


async def _lexical(store, query: str, *, collection: str = "papers", limit: int = 10,
                   exclude: frozenset[str] = frozenset()) -> list[str]:
    chunks = await _orchestrator(store)._search_lexical_sqlite(
        collection, query, limit, exclude_chunk_ids=exclude
    )
    return [c.chunk_id for c in chunks]


async def _index_rows(store) -> int:
    async with store._db.execute("SELECT count(*) FROM chunk_search_rows") as cursor:
        return (await cursor.fetchone())[0]


# ── 排序与匹配 ──────────────────────────────────────────────


async def test_stopword_heavy_question_ranks_limitations_first(store) -> None:
    await _doc(store, "paper", [
        "In this paper, the model of the system is the focus of the study. It is in the "
        "nature of the work that the scope of this is the set of the tasks in the field.",
        "The encoder of the network uses attention in each of the layers.",
        "Limitations. The main limitation of our approach is that it cannot handle long inputs.",
    ])
    result = await _orchestrator(store).retrieve_with_outcome(
        "papers", "what is the main limitation of the model in this paper", top_k=3
    )
    assert result.chunks[0].chunk_id == "paper:2"
    assert "sqlite_lexical" in result.engines


async def test_stemming_matches_plural_and_singular(store) -> None:
    await _doc(store, "stem", [
        "We discuss several limitations of transformers.",
        "Unrelated text about datasets.",
    ])
    assert await _lexical(store, "limitation") == ["stem:0"]
    assert await _lexical(store, "transformer") == ["stem:0"]
    # 反方向（复数查单数）子串匹配做不到，只有词干能做到。
    await _doc(store, "singular", ["A known limitation of each benchmark."])
    assert await _lexical(store, "limitations benchmarks") == ["singular:0", "stem:0"]


async def test_long_chinese_question_hits_body_paragraph(store) -> None:
    await _doc(store, "zh", [
        "视觉空间模板负责短暂保持图像信息。",
        "实验二发现，中央执行系统负荷越高，被试的数字广度成绩越低。",
    ])
    result = await _orchestrator(store).retrieve_with_outcome(
        "papers", "中央执行系统负荷越高数字广度会怎样", top_k=2
    )
    assert result.chunks[0].chunk_id == "zh:1"


@pytest.mark.parametrize("query", [
    '"unbalanced quote', "AND OR NOT", "body:attention", "atten*", "(attention",
    "NEAR(attention model)", "^attention", "-attention", "attention's model", "a + b = c",
    "{body} : *", "''", '"""',
])
async def test_fts_syntax_characters_never_raise(store, query: str) -> None:
    await _doc(store, "syntax", ["attention model body"])
    ids = await _lexical(store, query)  # 直接调通道：异常不会被编排器的 try/except 吞掉
    assert ids in ([], ["syntax:0"])


async def test_limit_and_excluded_chunks_are_applied_in_sql(store) -> None:
    await _doc(store, "many", [f"attention variant {i}" for i in range(5)])
    assert len(await _lexical(store, "attention", limit=2)) == 2
    ids = await _lexical(store, "attention", exclude=frozenset({"many:0", "many:1"}))
    assert set(ids) == {"many:2", "many:3", "many:4"}


# ── 候选范围 ────────────────────────────────────────────────


async def test_candidate_scope_excludes_detached_and_other_collections(store) -> None:
    await _doc(store, "active", ["zebrafish regeneration"])
    await _doc(store, "elsewhere", ["zebrafish regeneration"], collection="other")
    await _doc(store, "gone", ["zebrafish regeneration"], state=DocumentLifecycle.DETACHED)

    assert await _lexical(store, "zebrafish") == ["active:0"]
    assert sorted(await _lexical(store, "zebrafish", collection="")) == [
        "active:0", "elsewhere:0",
    ]


# ── 索引同步 ────────────────────────────────────────────────


async def test_replace_chunks_reindexes_and_drops_old_text(store) -> None:
    await _doc(store, "doc", ["original wording about axolotls"])
    await store.replace_chunks(
        "doc", [DocumentChunk("doc:0", "doc", 0, "new wording on newts", "h")]
    )
    assert await _lexical(store, "axolotls") == []
    assert await _lexical(store, "newts") == ["doc:0"]
    assert await _index_rows(store) == 1


async def test_processing_commit_reindexes(store) -> None:
    await _doc(store, "proc", ["before processing"])
    doc = await store.get_document("proc")
    await store.commit_document_processing(
        doc, [DocumentChunk("proc:0", "proc", 0, "after processing pangolins", "h2")],
        [PageChunk("proc", 1, 0, 10)],
    )
    assert await _lexical(store, "pangolins") == ["proc:0"]
    assert await _lexical(store, "before") == []
    assert await _index_rows(store) == 1


async def test_document_delete_cascades_to_keyword_index(store) -> None:
    await _doc(store, "del", ["capybara habitat", "capybara diet"])
    assert await _index_rows(store) == 2
    assert await store.delete_document("del")
    assert await _index_rows(store) == 0
    async with store._db.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'capybara'"
    ) as cursor:
        assert (await cursor.fetchone())[0] == 0


async def test_backfill_indexes_chunks_written_outside_the_store(store) -> None:
    await _doc(store, "raw", [])
    # 绕过写入点（如旧库导入 / 迁移前就有的分块）：没有索引，关键词召回不到。
    await store._db.execute(
        "INSERT INTO chunks (chunk_id, doc_id, ordinal, text, content_hash, metadata) "
        "VALUES ('raw:0', 'raw', 0, '离线安装后仍然可以召回 okapi', 'h', '{}')"
    )
    await store._db.commit()
    assert await _lexical(store, "okapi") == []

    assert await backfill_search_index(store._db) == 1
    assert await _lexical(store, "okapi") == ["raw:0"]
    assert await _lexical(store, "离线安装") == ["raw:0"]
    assert await backfill_search_index(store._db) == 0


async def test_text_update_drops_stale_index_until_backfill(store) -> None:
    await _doc(store, "upd", ["walrus colony"])
    await store._db.execute("UPDATE chunks SET text = 'seal colony' WHERE chunk_id = 'upd:0'")
    await store._db.commit()
    assert await _lexical(store, "walrus") == []
    assert await backfill_search_index(store._db) == 1
    assert await _lexical(store, "seal") == ["upd:0"]
