-- 029_external_sources.sql
-- 回答里引用的外部文献的出处账本（3.0.0「联网补充」）。
--
-- 记账方式参照 ai-source-scraper 1.7.0：一次联网补充 = 一个 capture（编号 + 时间），
-- 每个索引给出的每一条记排名（rank），同一对话内用 seen-set 判断「首次出现」（is_new），
-- 排名变化即「漂移」（由 rank 现算，不另存）。在此之上加了 scraper 没有的两层：
--   - web_sources.trust：按持久标识与第二个索引查证的结果（kacore/domain/web_sources.py 的 trust_level）；
--   - web_citations：回答里真正引用的那一段原文 / 摘要、页码与当时读到的文件指纹，缓存清掉后仍可核对。
-- web_source_fields 是字段级出处：标题、摘要等各取自哪个索引、何时取得。
--
-- 只新增表，不改既有表。对话由 chat_history 的 conv_id 标识（不设外键：对话可被删除，账本保留供导出）。

CREATE TABLE IF NOT EXISTS web_captures (
    capture_id  TEXT PRIMARY KEY,
    conv_id     TEXT NOT NULL,
    msg_idx     INTEGER NOT NULL,             -- 对话中第几条消息（回答）
    question    TEXT NOT NULL,                -- 用户原话
    queries     TEXT NOT NULL DEFAULT '[]',   -- JSON：模型为库里缺的部分写的检索词
    engines     TEXT NOT NULL DEFAULT '[]',   -- JSON：本次启用的索引
    actor       TEXT NOT NULL CHECK (actor IN ('ui', 'agent')),
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_captures_conv ON web_captures(conv_id, captured_at);

CREATE TABLE IF NOT EXISTS web_sources (
    source_id   TEXT PRIMARY KEY,             -- canonical_id：doi:… / arxiv:… / pmid:… / openalex:… / url:…
    trust       TEXT NOT NULL
                CHECK (trust IN ('verified', 'single', 'conflict', 'retracted', 'unresolved')),
    aliases     TEXT NOT NULL DEFAULT '[]',   -- JSON：同一篇的其它标识
    meta        TEXT NOT NULL DEFAULT '{}',   -- JSON：作者、年份、标题、期刊、类型、OA 地址、摘要
    reference_ids TEXT NOT NULL DEFAULT '[]', -- JSON：参考文献标识（引用图谱的边）
    placement   TEXT NOT NULL DEFAULT '{}',   -- JSON：按用户集合树的归类与理由（用户配置的模型给出）
    first_seen  TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_capture_hits (
    capture_id  TEXT NOT NULL REFERENCES web_captures(capture_id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL REFERENCES web_sources(source_id),
    engine      TEXT NOT NULL,
    query       TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    is_new      INTEGER NOT NULL DEFAULT 1,
    request_url TEXT NOT NULL DEFAULT '',     -- 去掉 key 的请求地址
    PRIMARY KEY (capture_id, source_id, engine, query)
);

CREATE INDEX IF NOT EXISTS idx_web_capture_hits_source ON web_capture_hits(source_id);

CREATE TABLE IF NOT EXISTS web_citations (
    capture_id  TEXT NOT NULL REFERENCES web_captures(capture_id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL REFERENCES web_sources(source_id),
    ord         INTEGER NOT NULL,             -- 回答里第几处引用
    passage     TEXT NOT NULL,                -- 引用的原文片段，或摘要
    page        INTEGER,                      -- 全文页码；只用了摘要时为空
    file_sha256 TEXT NOT NULL DEFAULT '',     -- 当时读到的全文文件指纹
    PRIMARY KEY (capture_id, ord)
);

CREATE TABLE IF NOT EXISTS web_source_fields (
    source_id   TEXT NOT NULL REFERENCES web_sources(source_id) ON DELETE CASCADE,
    field       TEXT NOT NULL,
    engine      TEXT NOT NULL,
    value       TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, field, engine)
);
