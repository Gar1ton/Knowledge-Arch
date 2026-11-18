-- 关键词召回（P31 ④）：分块派生检索文本的 FTS5（BM25）索引，替代 `text LIKE` 全量扫描。
--
-- 只新增表与触发器，不改 chunks、不改向量、不触发重新分块或重新嵌入。
--
-- 1) chunk_search_rows：chunk_id ↔ FTS 行号。chunks 的主键是 TEXT，隐式 rowid 在 VACUUM 时可能被
--    重新编号，所以 FTS 不直接挂 chunks.rowid，而是挂这张表显式的 INTEGER PRIMARY KEY。
-- 2) chunks_fts：contentless（不存正文，只存倒排），可按行号删除（contentless_delete=1，SQLite ≥ 3.43）。
--    body 是 `kacore/utils/search_text.py` 的 search_text(chunks.text)：汉字 / 假名已拆成二元组，
--    tokenizer 再做小写、去重音与 porter 词干（limitation ↔ limitations）。
-- 3) 写入：派生文本要用 Python 算，SQL 迁移里算不了。分块写入点（source_store 的 replace_chunks 与
--    processing.commit_processing）在同一事务内补索引；本迁移不回填，已有分块由启动时的
--    `backfill_search_index` 一次补齐（它也补上任何绕过写入点进来的分块，例如旧库导入、R2 恢复）。
-- 4) 删除 / 改文本：触发器同步删掉索引行，覆盖显式 DELETE 与删文档时的 ON DELETE CASCADE。
--    改过文本的分块由下一次启动回填重新索引（当前代码没有原地 UPDATE chunks 的路径）。

CREATE TABLE IF NOT EXISTS chunk_search_rows (
    row INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body,
    content='',
    contentless_delete=1,
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_search_after_delete AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts
        WHERE rowid IN (SELECT row FROM chunk_search_rows WHERE chunk_id = old.chunk_id);
    DELETE FROM chunk_search_rows WHERE chunk_id = old.chunk_id;
END;

CREATE TRIGGER IF NOT EXISTS chunks_search_after_update AFTER UPDATE OF chunk_id, text ON chunks BEGIN
    DELETE FROM chunks_fts
        WHERE rowid IN (SELECT row FROM chunk_search_rows WHERE chunk_id = old.chunk_id);
    DELETE FROM chunk_search_rows WHERE chunk_id = old.chunk_id;
END;
