-- 030_zotero_push_ledger.sql
-- 本地文档推送回 Zotero 的账本（3.0.0）。
--
-- 推送 = 移交：KA 把文件按用户的命名风格移进 ZotMoov 目录（不留副本），再用 Zotero Web API 建一条
-- 独立的 linked_file 附件指向它。下次 Zotero 拉取时，镜像按本表把条目「认领」回原文档（保留 doc_id、
-- 切块与向量），而不是另建一篇重复文档（kacore/pipelines/zotero_sync_pipeline.py）。
-- source_path 记录移交前的位置：写入 Zotero 失败时据此把文件搬回。

CREATE TABLE IF NOT EXISTS zotero_push_ledger (
    doc_id      TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    library_id  TEXT NOT NULL DEFAULT '',
    item_key    TEXT NOT NULL DEFAULT '',       -- 独立附件的 key（拉取时据此认领）
    target_coll TEXT NOT NULL DEFAULT '',       -- 目标 Zotero 集合 key
    file_path   TEXT NOT NULL DEFAULT '',       -- 放进 ZotMoov 目录后的位置
    source_path TEXT NOT NULL DEFAULT '',       -- 移交前的 KA 位置
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'placing', 'creating', 'pushed', 'adopted', 'failed')),
    message     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    pushed_at   TEXT,
    adopted_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_zotero_push_ledger_item ON zotero_push_ledger(library_id, item_key);
