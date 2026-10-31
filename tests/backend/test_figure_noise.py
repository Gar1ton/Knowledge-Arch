"""正文去图表污染（2.1.0 P3）：L1 文本规则、L2 遮罩、图块、旧文档升级路径。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kacore.config import SourceStoreConfig
from kacore.domain.figures import (
    CHUNK_KIND_FIGURE,
    FIGURE_DONE,
    DocumentFigure,
    figure_chunk_text,
)
from kacore.domain.models import SourceDocument
from kacore.managers.artifact_provenance import PROCESSING_VERSION, save_provenance
from kacore.managers.figure_detection import detect_figures_in_file
from kacore.managers.figure_noise import is_mojibake, is_pseudo_table, strip_figure_noise
from kacore.managers.ingest_manager import IngestManager
from kacore.managers.markdown_extractor import MarkdownArtifact, PageSpan, extract_pdf_markdown
from kacore.repository.source_store.memory import InMemorySourceDocumentStore

fitz = pytest.importorskip("pymupdf")

PSEUDO_TABLE = [
    "|Col1|Col2|Col3|",
    "|---|---|---|",
    "||||",
    "|||~~ViT-B/32~~<br>~~BiT~~|",
    "||||",
]
REAL_TABLE = [
    "|model|top-1 err.|top-5 err.|",
    "|---|---|---|",
    "|VGG-16|28.07|9.33|",
    "|ResNet-152|21.43|5.71|",
]


# ── L1 规则 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GĮŔũƜ ˤˤƛĎĵũ", True),
        ("ɾ ĭ ò ƛ ŝ ũ ƓíĎʛ Ŕĭ Į đˤ", True),
        ("Zażółć gęślą jaźń — polski tekst jest w porządku.", False),
        ("Tiếng Việt có dấu vẫn là văn bản bình thường.", False),
        ("注意力机制与 Transformer", False),
        ("ImageNet top-1 accuracy", False),
    ],
)
def test_mojibake_detection(text: str, expected: bool) -> None:
    assert is_mojibake(text) is expected


def test_pseudo_tables_are_recognised_and_real_tables_kept() -> None:
    assert is_pseudo_table(PSEUDO_TABLE)
    assert not is_pseudo_table(REAL_TABLE)
    # 表头缺失的真表格（ColN 表头但格子都有数）也保留。
    filled = ["|Col1|Col2|", "|---|---|", "|ResNet-50|22.85|", "|ResNet-101|21.75|"]
    assert not is_pseudo_table(filled)


def test_strip_removes_pseudo_tables_mojibake_and_tick_runs() -> None:
    page = "\n".join([
        "Results improve with scale.", "", "90", "", "85", "", "80", "",
        *PSEUDO_TABLE, "", "GĮŔũƜ ˤˤƛĎĵũ", "", *REAL_TABLE, "",
        "We report 3 runs.", "42",
    ])

    cleaned = strip_figure_noise(page)

    assert "Col1" not in cleaned and "GĮŔũƜ" not in cleaned
    assert "\n90\n" not in cleaned and "85" not in cleaned
    assert "|ResNet-152|21.43|5.71|" in cleaned
    assert "We report 3 runs." in cleaned and "42" in cleaned  # 单个数字不是刻度串
    assert strip_figure_noise(cleaned) == cleaned  # 幂等


def test_strip_can_preserve_offsets() -> None:
    page = "Body text.\n" + "\n".join(PSEUDO_TABLE) + "\nMore body."

    blanked = strip_figure_noise(page, preserve_offsets=True)

    assert len(blanked) == len(page)
    assert blanked.index("More body.") == page.index("More body.")


# ── L2 遮罩 + 图块 ───────────────────────────────────────────


def _chart_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(72, 80, 520, 200),
        "Residual learning eases the optimisation of very deep networks. " * 4,
        fontsize=10,
    )
    box = fitz.Rect(120, 230, 420, 390)
    page.draw_rect(box)
    for i in range(1, 6):
        page.draw_line((box.x0, box.y0 + i * 26), (box.x1, box.y0 + i * 26), color=(0.8, 0.8, 0.8))
    for i in range(1, 8):
        page.draw_line((box.x0 + i * 37, box.y0), (box.x0 + i * 37, box.y1), color=(0.8, 0.8, 0.8))
    for i, tick in enumerate(("90", "85", "80", "75")):
        page.insert_text((box.x0 - 18, box.y0 + 12 + i * 40), tick, fontsize=7)
    page.insert_text((box.x0 + 110, box.y1 + 12), "Pre-training dataset", fontsize=7)
    page.insert_textbox(
        fitz.Rect(72, 412, 520, 440), "Figure 1: Transfer accuracy versus dataset size.", fontsize=9
    )
    page.insert_textbox(
        fitz.Rect(72, 460, 520, 560),
        "Larger datasets help the transformer more than the convolutional baseline. " * 3,
        fontsize=10,
    )
    doc.save(path)
    doc.close()
    return path


def test_masking_moves_figure_text_out_of_the_body(tmp_path: Path) -> None:
    pdf = _chart_pdf(tmp_path / "chart.pdf")
    scan = detect_figures_in_file(str(pdf), "DOC")

    artifact = extract_pdf_markdown(str(pdf), figure_scan=scan)

    body = artifact.clean_markdown
    assert "Pre-training dataset" not in body
    assert "Figure 1: Transfer accuracy" in body  # 标题留在正文
    assert "Residual learning" in body and "Larger datasets" in body
    assert "Col1" not in body
    assert "Pre-training dataset" in scan.figures[0].inner_text  # 没有丢：挪进了图位


async def test_figure_chunks_follow_body_chunks(tmp_path: Path) -> None:
    store = InMemorySourceDocumentStore()
    manager = IngestManager(source_store=store, config=SourceStoreConfig(), data_dir=tmp_path)
    pdf = _chart_pdf(tmp_path / "chart.pdf")

    doc_id = await manager.ingest(
        title="chart", file_path=str(pdf), content_type="application/pdf",
        size_bytes=pdf.stat().st_size, collection="papers",
    )
    chunks = await store.list_chunks(doc_id)
    figure_chunks = [c for c in chunks if c.metadata.get("chunk_kind") == CHUNK_KIND_FIGURE]
    assert len(figure_chunks) == 1
    fig_chunk = figure_chunks[0]
    assert fig_chunk.ordinal == len(chunks) - 1  # 排在正文块之后
    assert fig_chunk.metadata["anchor_labels"] == ["FIGURE 1"]
    assert fig_chunk.text.startswith("Figure 1 (page 1)")
    assert "Text in figure:" in fig_chunk.text and "Pre-training dataset" in fig_chunk.text
    doc = await store.get_document(doc_id)
    assert doc is not None
    assert not IngestManager.chunk_needs_rebuild(doc_id, chunks, doc.local_meta)


def test_figure_chunk_text_formats_description_and_is_empty_without_content() -> None:
    fig = DocumentFigure("D_fig002_00", "D", 2, 0, (0, 0, 1, 1), label="Figure 4",
                         caption="Figure 4: Loss curves.", inner_text="iter\n0.5",
                         status=FIGURE_DONE, description="Two loss curves fall.")
    text = figure_chunk_text(fig)
    assert text.split("\n\n") == [
        "Figure 4 (page 2)", "Figure 4: Loss curves.",
        "Figure description (generated by a vision model): Two loss curves fall.",
        "Text in figure: iter · 0.5",
    ]
    assert figure_chunk_text(DocumentFigure("x", "D", 1, 0, (0, 0, 1, 1))) == ""


# ── 旧文档升级 ────────────────────────────────────────────────


async def test_stale_document_without_pdf_is_recleaned_from_raw_pages(tmp_path: Path) -> None:
    """原 PDF 不在时，版本过期的文档从 provenance 原始页重洗（L1）。"""
    store = InMemorySourceDocumentStore()
    manager = IngestManager(source_store=store, config=SourceStoreConfig(), data_dir=tmp_path)
    bundle = tmp_path / "library" / "old"
    bundle.mkdir(parents=True)
    raw = "Intro paragraph.\n\n" + "\n".join(PSEUDO_TABLE) + "\n\nClosing paragraph."
    polluted = raw  # 旧版清洗把伪表格留在了 clean.md 里
    artifact = MarkdownArtifact(polluted, [PageSpan(1, 0, len(polluted))], raw_pages=[raw])
    save_provenance(bundle, artifact)
    (bundle / "clean.md").write_text(polluted, encoding="utf-8")
    (bundle / "pages.json").write_text(json.dumps(
        [{"page": 1, "markdown_start_char": 0, "markdown_end_char": len(polluted)}]
    ))
    await store.add_document(SourceDocument(
        "old", "Old", str(tmp_path / "missing.pdf"), "application/pdf", 1, "h", "papers",
        markdown_rel_path="clean.md", pages_rel_path="pages.json",
        local_meta={"processing_version": "cleaning-v3"},
    ))

    await manager.rebuild_document_chunks_from_artifact("old")

    text = "\n".join(c.text for c in await store.list_chunks("old"))
    assert "Col1" not in text and "Intro paragraph." in text and "Closing paragraph." in text
    doc = await store.get_document("old")
    assert doc is not None and doc.local_meta["processing_version"] == PROCESSING_VERSION


async def test_stale_pdf_document_is_reextracted_with_figures(tmp_path: Path) -> None:
    """原 PDF 还在时，版本过期的文档从原 PDF 重抽取：补上图空位与图块。"""
    store = InMemorySourceDocumentStore()
    manager = IngestManager(source_store=store, config=SourceStoreConfig(), data_dir=tmp_path)
    pdf = _chart_pdf(tmp_path / "chart.pdf")
    doc_id = await manager.ingest(
        title="chart", file_path=str(pdf), content_type="application/pdf",
        size_bytes=pdf.stat().st_size, collection="papers",
    )
    await store.replace_document_figures(doc_id, [])
    doc = await store.get_document(doc_id)
    assert doc is not None
    doc.local_meta = {**doc.local_meta, "processing_version": "cleaning-v3"}
    await store.update_document(doc)

    await manager.rebuild_document_chunks_from_artifact(doc_id)

    assert [f.label for f in await store.list_document_figures(doc_id)] == ["Figure 1"]
    kinds = [c.metadata.get("chunk_kind") for c in await store.list_chunks(doc_id)]
    assert CHUNK_KIND_FIGURE in kinds
