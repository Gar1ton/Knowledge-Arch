"""找图与图空位（2.1.0 P2）：合成 PDF 上的检测、两种仓储实现的契约、摄入接线。"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from kacore.config import SourceStoreConfig
from kacore.domain.figures import (
    FIGURE_DONE,
    FIGURE_FAILED,
    FIGURE_PENDING,
    KIND_RASTER,
    KIND_VECTOR,
    DocumentFigure,
    figure_id_for,
    merge_figure_descriptions,
)
from kacore.managers.figure_detection import detect_figures_in_file, render_figure_png
from kacore.managers.ingest_manager import META_SCANNED_PAGES, IngestManager
from kacore.migration_runner import run_migrations
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.source_store.sqlite import SQLiteSourceDocumentStore

fitz = pytest.importorskip("pymupdf")

BODY = (
    "Residual learning eases the optimisation of very deep networks because each block only "
    "needs to fit a small correction to the identity mapping, which keeps gradients healthy. "
)


def _chart(page, top: float, caption: str) -> None:
    """一张矢量折线图：坐标框 + 网格 + 折线 + 刻度 / 轴标题，标题在图下方。"""
    box = fitz.Rect(120, top, 420, top + 160)
    page.draw_rect(box, color=(0, 0, 0))
    for i in range(1, 6):
        page.draw_line((box.x0, box.y0 + i * 26), (box.x1, box.y0 + i * 26), color=(0.8, 0.8, 0.8))
    points = [(box.x0 + i * 30, box.y1 - 10 - (i % 4) * 30) for i in range(10)]
    for a, b in zip(points, points[1:]):
        page.draw_line(a, b, color=(1, 0, 0), width=1.5)
    for i, tick in enumerate(("90", "85", "80", "75")):
        page.insert_text((box.x0 - 18, box.y0 + 12 + i * 40), tick, fontsize=7)
    page.insert_text((box.x0 + 110, box.y1 + 12), "Pre-training dataset", fontsize=7)
    page.insert_textbox(fitz.Rect(72, box.y1 + 22, 520, box.y1 + 50), caption, fontsize=9)


def _table(page, top: float) -> None:
    """一张有字的线框表，标题 Table 1 在表上方。"""
    page.insert_text((72, top - 6), "Table 1: Top-1 error on ImageNet.", fontsize=9)
    rows, cols = 4, 3
    for r in range(rows + 1):
        page.draw_line((120, top + r * 18), (420, top + r * 18))
    for c in range(cols + 1):
        page.draw_line((120 + c * 100, top), (120 + c * 100, top + rows * 18))
    for r in range(rows):
        for c in range(cols):
            page.insert_text((126 + c * 100, top + 13 + r * 18), f"r{r}c{c} 2{r}.{c}", fontsize=8)


def _logo(page) -> None:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 80), 0)
    pix.set_rect(pix.irect, (30, 90, 200))
    page.insert_image(fitz.Rect(500, 20, 560, 80), pixmap=pix)


def _make_pdf(path: Path) -> Path:
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_textbox(fitz.Rect(72, 90, 520, 200), BODY * 3, fontsize=10)
    _chart(p1, 230, "Figure 1: Transfer accuracy versus pre-training dataset size.")
    p1.insert_textbox(fitz.Rect(72, 460, 520, 600), BODY * 3, fontsize=10)
    _logo(p1)
    p2 = doc.new_page()
    p2.insert_textbox(fitz.Rect(72, 80, 520, 180), BODY * 3, fontsize=10)
    _table(p2, 230)
    _logo(p2)
    p3 = doc.new_page()  # 扫描页：整页一张图，几乎没有文字
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400), 0)
    pix.set_rect(pix.irect, (240, 240, 230))
    p3.insert_image(p3.rect, pixmap=pix)
    _logo(p3)
    doc.save(path)
    doc.close()
    return path


# ── 检测 ──────────────────────────────────────────────────────


def test_detects_captioned_chart_and_moves_axis_labels(tmp_path: Path) -> None:
    scan = detect_figures_in_file(str(_make_pdf(tmp_path / "paper.pdf")), "DOC")

    assert [(f.page, f.label, f.kind) for f in scan.figures] == [(1, "Figure 1", KIND_VECTOR)]
    fig = scan.figures[0]
    assert fig.figure_id == figure_id_for("DOC", 1, 0) == "DOC_fig001_00"
    assert fig.caption.startswith("Figure 1: Transfer accuracy")
    # 刻度与紧贴图外的轴标题并入图内文字；正文段落与标题不并入。
    for label in ("90", "85", "Pre-training dataset"):
        assert label in fig.inner_text
    assert "Residual learning" not in fig.inner_text
    assert "Transfer accuracy" not in fig.inner_text


def test_skips_real_tables_repeated_logos_and_reports_scanned_pages(tmp_path: Path) -> None:
    scan = detect_figures_in_file(str(_make_pdf(tmp_path / "paper.pdf")), "DOC")

    assert all(f.page == 1 for f in scan.figures)  # 第 2 页的表格与各页 logo 都不算图
    assert not any(f.kind == KIND_RASTER for f in scan.figures)
    assert scan.scanned_pages == [3]


def test_render_figure_png_is_capped(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "paper.pdf")
    fig = detect_figures_in_file(str(pdf), "DOC").figures[0]

    png = render_figure_png(str(pdf), fig.page, fig.bbox, dpi=600, max_pixels=200_000)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pix = fitz.Pixmap(png)
    assert pix.width * pix.height <= 200_000 * 1.05


def test_merge_keeps_previous_descriptions_at_the_same_position() -> None:
    fresh = [
        DocumentFigure("D_fig001_00", "D", 1, 0, (100.4, 200.2, 300.0, 400.0), caption="new"),
        DocumentFigure("D_fig002_00", "D", 2, 0, (50, 50, 200, 200)),
    ]
    old = [
        DocumentFigure("D_fig001_00", "D", 1, 0, (101.0, 199.8, 300.3, 400.9),
                       status=FIGURE_DONE, description="a line chart", model="m", image_hash="h"),
        DocumentFigure("D_fig003_00", "D", 3, 0, (0, 0, 10, 10), status=FIGURE_DONE),
    ]

    merged = merge_figure_descriptions(fresh, old)

    assert [(f.figure_id, f.status) for f in merged] == [
        ("D_fig001_00", FIGURE_DONE), ("D_fig002_00", FIGURE_PENDING),
    ]
    assert merged[0].description == "a line chart" and merged[0].caption == "new"


# ── 仓储契约（内存 / SQLite 同一组用例）──────────────────────


@pytest.fixture(params=["memory", "sqlite"])
async def store(request):
    if request.param == "memory":
        yield InMemorySourceDocumentStore()
        return
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)
    yield SQLiteSourceDocumentStore(conn)
    await conn.close()


async def _add_doc(store, doc_id: str) -> None:
    from datetime import datetime, timezone

    from kacore.domain.models import SourceDocument

    now = datetime.now(timezone.utc)
    await store.add_document(SourceDocument(
        doc_id=doc_id, title=doc_id, file_path=f"/{doc_id}.pdf", content_type="application/pdf",
        size_bytes=1, content_hash=doc_id, collection="default", tags=[],
        created_at=now, updated_at=now,
    ))


def _fig(doc_id: str, page: int, ordinal: int, status: str = FIGURE_PENDING) -> DocumentFigure:
    return DocumentFigure(
        figure_id_for(doc_id, page, ordinal), doc_id, page, ordinal, (1.0, 2.0, 3.0, 4.0),
        label=f"Figure {page}", status=status, updated_at="t0",
    )


async def test_store_replace_list_update_count(store) -> None:
    await _add_doc(store, "A")
    await _add_doc(store, "B")
    await store.replace_document_figures("A", [_fig("A", 2, 0), _fig("A", 1, 0)])
    await store.replace_document_figures("B", [_fig("B", 1, 0, FIGURE_FAILED)])

    listed = await store.list_document_figures("A")
    assert [f.figure_id for f in listed] == ["A_fig001_00", "A_fig002_00"]
    assert listed[0].bbox == (1.0, 2.0, 3.0, 4.0)

    missing = await store.list_figures(statuses={FIGURE_PENDING, FIGURE_FAILED})
    assert [f.figure_id for f in missing] == ["A_fig001_00", "A_fig002_00", "B_fig001_00"]
    assert [f.figure_id for f in await store.list_figures(doc_ids=["B"])] == ["B_fig001_00"]
    assert await store.list_figures(doc_ids=[]) == []

    done = listed[0]
    done.status, done.description, done.model, done.updated_at = FIGURE_DONE, "bar chart", "m", "t1"
    assert await store.update_figure_result(done) is True
    assert (await store.list_document_figures("A"))[0].description == "bar chart"
    assert await store.count_figures_by_status() == {
        FIGURE_DONE: 1, FIGURE_PENDING: 1, FIGURE_FAILED: 1,
    }
    assert await store.count_figures_by_status(["A"]) == {FIGURE_DONE: 1, FIGURE_PENDING: 1}

    await store.replace_document_figures("A", [_fig("A", 3, 0)])
    assert [f.figure_id for f in await store.list_document_figures("A")] == ["A_fig003_00"]
    ghost = _fig("A", 9, 9)
    assert await store.update_figure_result(ghost) is False


async def test_deleting_a_document_drops_its_figures(store) -> None:
    await _add_doc(store, "A")
    await store.replace_document_figures("A", [_fig("A", 1, 0)])

    await store.delete_document("A")

    assert await store.list_figures() == []


# ── 摄入接线 ──────────────────────────────────────────────────


def _manager(store, data_dir: Path) -> IngestManager:
    config = SourceStoreConfig(db_filename="t.db", default_collection="papers", ocr_enabled=False)
    return IngestManager(source_store=store, config=config, data_dir=data_dir)


async def test_ingest_stores_figure_slots_and_reextract_keeps_descriptions(tmp_path: Path) -> None:
    store = InMemorySourceDocumentStore()
    manager = _manager(store, tmp_path)
    pdf = _make_pdf(tmp_path / "paper.pdf")

    doc_id = await manager.ingest(
        title="paper", file_path=str(pdf), content_type="application/pdf",
        size_bytes=pdf.stat().st_size, collection="papers",
    )

    figures = await store.list_document_figures(doc_id)
    assert [(f.label, f.status, f.doc_id) for f in figures] == [
        ("Figure 1", FIGURE_PENDING, doc_id),
    ]
    doc = await store.get_document(doc_id)
    assert doc is not None and doc.local_meta[META_SCANNED_PAGES] == [3]

    described = figures[0]
    described.status, described.description = FIGURE_DONE, "A red line chart rising to 90."
    await store.update_figure_result(described)
    await manager.reextract_document(doc_id)

    again = await store.list_document_figures(doc_id)
    assert again[0].status == FIGURE_DONE
    assert again[0].description == "A red line chart rising to 90."


async def test_text_free_pdf_is_not_rechunked_on_every_listing(tmp_path: Path) -> None:
    """扫描件切不出 chunk：处理过一次后不应每次列文档都被判为需要重切。"""
    store = InMemorySourceDocumentStore()
    manager = _manager(store, tmp_path)
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 260), 0)
    pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pix)
    pdf = tmp_path / "scan.pdf"
    doc.save(pdf)
    doc.close()

    doc_id = await manager.ingest(
        title="scan", file_path=str(pdf), content_type="application/pdf",
        size_bytes=pdf.stat().st_size, collection="papers",
    )

    stored = await store.get_document(doc_id)
    assert stored is not None
    assert await store.list_chunks(doc_id) == []
    assert IngestManager.chunk_needs_rebuild(doc_id, [], stored.local_meta) is False


def test_photo_like_figures_are_sent_as_jpeg(tmp_path: Path) -> None:
    """照片类的图 PNG 很大：改用 JPEG（像素不变），线框图仍是 PNG。"""
    import random

    from kacore.managers.figure_detection import render_figure_image

    doc = fitz.open()
    page = doc.new_page()
    rng = random.Random(7)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 900, 700), 0)
    pix.set_rect(pix.irect, (120, 120, 120))
    noise = bytes(rng.randrange(256) for _ in range(900 * 700 * 3))
    photo = fitz.Pixmap(fitz.csRGB, 900, 700, noise, False)
    page.insert_image(fitz.Rect(72, 72, 520, 420), pixmap=photo)
    pdf = tmp_path / "photo.pdf"
    doc.save(pdf)
    doc.close()

    image, media_type = render_figure_image(str(pdf), 1, (72, 72, 520, 420))
    assert media_type == "image/jpeg" and image[:2] == b"\xff\xd8"
    chart = _make_pdf(tmp_path / "paper.pdf")
    fig = detect_figures_in_file(str(chart), "DOC").figures[0]
    _, chart_type = render_figure_image(str(chart), fig.page, fig.bbox)
    assert chart_type == "image/png"
