-- PDF 图表空位（2.1.0 识图）：上传时本地找图写入 pending，识图任务补 description。
-- bbox 为该页 PDF 坐标（点）的 JSON [x0, y0, x1, y1]；截图不落盘，识图时按位置从原 PDF 现渲染。
-- inner_text 是图区域内的文字（坐标刻度、图例），正文清洗把它们从正文挪到这里。
CREATE TABLE IF NOT EXISTS document_figures (
    figure_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    bbox TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    inner_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    description TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    image_hash TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_figures_doc ON document_figures(doc_id, page, ordinal);
CREATE INDEX IF NOT EXISTS idx_document_figures_status ON document_figures(status);
