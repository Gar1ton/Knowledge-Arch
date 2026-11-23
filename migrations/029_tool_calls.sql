-- 029_tool_calls.sql
-- 联网工具的调用账本（3.0.0「联网补充」）。
--
-- 每个联网功能是一个工具（kacore/adapters/tools），distributor 按问题挑工具调用，AI 助手经 skill 也调用
-- 同一批工具。这里记三样：
--   web_captures   一次运行（一个回答的联网补充，或助手 / 工具面板的一组调用）；
--   web_tool_calls 每次工具调用：参数、模型当时看到的原文块（含 cite 本身，被拒绝的登记也留痕）；
--   web_citations  通过可追溯核验的外部引用：段落取自哪次调用的哪一块、书目字段、回答是否用到。
-- 引用的出处全部能回到 web_tool_calls.blocks 里的原文（kacore/domain/tool_calls.py 的 trace_check）。
--
-- 取代开发期的 029_external_sources / 030_zotero_push_ledger（两者都没有发布过）：开发库上已建过的
-- 旧表在这里删掉；全新的库上 DROP 不起作用。对话由 chat_history 的 conv_id 标识（不设外键：对话可被删除，
-- 账本保留供导出）。

DROP TABLE IF EXISTS web_capture_hits;
DROP TABLE IF EXISTS web_source_fields;
DROP TABLE IF EXISTS web_citations;
DROP TABLE IF EXISTS web_sources;
DROP TABLE IF EXISTS web_captures;
DROP TABLE IF EXISTS zotero_push_ledger;

CREATE TABLE web_captures (
    capture_id  TEXT PRIMARY KEY,
    conv_id     TEXT NOT NULL,
    msg_idx     INTEGER NOT NULL,             -- 对话中第几条消息（回答）；助手 / 工具面板为 0
    question    TEXT NOT NULL,
    actor       TEXT NOT NULL CHECK (actor IN ('ui', 'agent', 'test')),
    captured_at TEXT NOT NULL
);

CREATE INDEX idx_web_captures_conv ON web_captures(conv_id, captured_at);
CREATE INDEX idx_web_captures_time ON web_captures(captured_at);

CREATE TABLE web_tool_calls (
    call_id     TEXT PRIMARY KEY,
    capture_id  TEXT NOT NULL REFERENCES web_captures(capture_id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,             -- 本次运行里的第几次调用
    tool        TEXT NOT NULL,
    arguments   TEXT NOT NULL DEFAULT '{}',   -- JSON
    blocks      TEXT NOT NULL DEFAULT '[]',   -- JSON [{text, locator, page}]：模型当时看到的原文
    is_error    INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    elapsed_ms  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_web_tool_calls_capture ON web_tool_calls(capture_id, ord);

CREATE TABLE web_citations (
    capture_id  TEXT NOT NULL REFERENCES web_captures(capture_id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,             -- E1、E2…
    call_id     TEXT NOT NULL REFERENCES web_tool_calls(call_id),
    block       INTEGER NOT NULL,             -- 段落所在的块（从 0 起）
    locator     TEXT NOT NULL DEFAULT '',     -- 该块的位置说明，如 page 5
    page        INTEGER,                      -- 该块的原文页码；没有页时为空
    fields      TEXT NOT NULL DEFAULT '{}',   -- JSON：title / creators / year / venue / identifier / url
    passage     TEXT NOT NULL,
    cited       INTEGER NOT NULL DEFAULT 0,   -- 回答正文用到了它
    placement   TEXT NOT NULL DEFAULT '{}',   -- JSON：按用户集合树的归类（体系视图）
    PRIMARY KEY (capture_id, ord)
);
