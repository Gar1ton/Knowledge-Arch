-- 030_paper_imports.sql
-- 3.0.0 外部论文入库与 Zotero 推送的两本账。
--
-- paper_imports  来源栏「入库」的每一次：取自哪次联网补充的第几条引用、书目字段、文件名、目标集合、
--                状态（待补 PDF / 已入库 / 已删掉）、下到的链接与文件指纹、试过的链接。
--                出处（对话、回答、问题、工具、调用、位置）入库时抄进 source，不依赖账本表还在不在，
--                所以 capture_id 不设外键。
-- zotero_pushes  本地文档推送到 Zotero 的每一篇：Zotero 附件 key、attachments: 相对路径、移动后的
--                文件位置、状态（已推送待拉取 / 已认领 / 失败）。拉取时按 attachment_key 找回原文档
--                认领，不按新编号重建（见 pipelines/zotero_sync_pipeline.py）。write_token 让重试
--                不会在 Zotero 里建出两条。
-- 两张表都是新表，已有表不动；不用这两个功能时它们一直是空的。

CREATE TABLE IF NOT EXISTS paper_imports (
    import_id   TEXT PRIMARY KEY,
    capture_id  TEXT NOT NULL,
    ord         INTEGER NOT NULL,
    fields      TEXT NOT NULL DEFAULT '{}',
    filename    TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending_pdf', 'imported', 'dismissed')),
    doc_id      TEXT NOT NULL DEFAULT '',
    pdf_url     TEXT NOT NULL DEFAULT '',
    pdf_sha256  TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '{}',
    tried       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_imports_capture ON paper_imports(capture_id, ord);
CREATE INDEX IF NOT EXISTS idx_paper_imports_status ON paper_imports(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_paper_imports_doc ON paper_imports(doc_id);

CREATE TABLE IF NOT EXISTS zotero_pushes (
    doc_id          TEXT PRIMARY KEY,
    library_id      TEXT NOT NULL,
    collection_key  TEXT NOT NULL,
    attachment_key  TEXT NOT NULL DEFAULT '',
    path            TEXT NOT NULL DEFAULT '',
    file_path       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('pushed', 'claimed', 'failed')),
    message         TEXT NOT NULL DEFAULT '',
    write_token     TEXT NOT NULL,
    pushed_at       TEXT NOT NULL,
    claimed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_zotero_pushes_attachment ON zotero_pushes(attachment_key);
