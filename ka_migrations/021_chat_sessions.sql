-- 021_chat_sessions.sql
-- 统一 Web/外部渠道会话设置，并为历史消息保存结构化生成快照。

CREATE TABLE IF NOT EXISTS chat_sessions (
    conversation_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL DEFAULT 'web',
    external_key TEXT NOT NULL DEFAULT '',
    research_enabled INTEGER NOT NULL DEFAULT 0,
    context_enabled INTEGER NOT NULL DEFAULT 1,
    persona_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_channel_external
    ON chat_sessions(channel, external_key)
    WHERE external_key <> '';

ALTER TABLE chat_history ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';

