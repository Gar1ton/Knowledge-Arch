-- 聊天记录的附加信息（JSON）：思考过程 thinking_trace、改写后的问题 standalone_question。
-- 超时补拉与重启后读历史时据此恢复，旧记录为 '{}'。
ALTER TABLE chat_history ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
