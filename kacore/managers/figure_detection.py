"""PDF 找图：按 PDF 结构（位图位置 + 矢量绘图簇 + 图表标题）找出每张图（managers 层，纯本地）。

为什么存在：识图要先知道「图在哪」。这里不调模型、不做 OCR，只读 PDF 自带的结构：
  1. 候选：位图（`get_image_info`）与矢量绘图簇（`cluster_drawings`），
     过滤过小的、跨多页重复的（logo）。
  2. 标题：以 `Figure / Fig. / 图 N` 开头的文字块是图标题，`Table / 表 N` 是表标题。
     PyMuPDF4LLM 会把矢量图表误判成表格，所以图 / 表只看标题，不信它的表格判断。
  3. 真表格：带表标题的线框表（`find_tables`）从候选里切掉，避免和相邻的图连成一片（ToT 论文实测）。
  4. 归属：候选配最近的标题（≤ 40pt）；配上图标题的同标题候选合并为一张图；配上表标题的丢弃；
     没有标题的只保留较大的位图（照片、截图类插图）。
  5. 图内文字：图区域内的文字行，加上紧贴图外 20pt 内的短行（坐标刻度、轴标题），按阅读顺序拼成
     `inner_text`，最终区域随之扩大。后续正文清洗据此把这些文字从正文挪进图位。
  6. 扫描页：几乎没有文字、却被一张大位图铺满的页，只记页码（不做 OCR），不当作图。

依赖方向：stdlib + domain + 懒加载的 PyMuPDF（`pymupdf`，正文抽取本就依赖它）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from kacore.domain.figures import (
    KIND_MIXED,
    KIND_RASTER,
    KIND_VECTOR,
    BBox,
    DocumentFigure,
    figure_id_for,
)

FIGURE_CAPTION_RE = re.compile(r"^\s*(?P<label>(?:Figure|Fig\.?|图)\s*(?:S?\d+[A-Za-z]?))", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*(?:Table|Tab\.?|表)\s*S?\d+", re.I)

# 尺寸阈值（经 Attention / ResNet / ViT / ToT 四篇实测调定）。
_MIN_RASTER_AREA_RATIO = 0.01
_MIN_RASTER_SIDE = 40.0
# 矢量候选必须配上图标题才保留，阈值可以放低（ResNet 的小残差块图只占页面 1%）。
_MIN_VECTOR_AREA_RATIO = 0.005
_MIN_VECTOR_SIDE = 40.0
_UNCAPTIONED_RASTER_AREA_RATIO = 0.04
_CAPTION_MAX_GAP = 40.0
# 图标题习惯在图下方：在图上方的标题计算距离时加罚，避免上下两张图抢同一个标题（ViT 第 7 页）。
_CAPTION_ABOVE_PENALTY = 20.0
# 已归属某标题的候选，相隔这么近的未归属候选并入同一张图（ResNet 图 3 的多列结构图）。
_SIBLING_MAX_GAP = 30.0
_TABLE_MIN_FILLED_RATIO = 0.5
_BODY_MIN_LINES = 2
_BODY_SIZE_TOLERANCE = 0.6
_BODY_MIN_AVG_CHARS = 50
# 与图标题同列、在标题上方这么近的未归属候选，算作同一张图（Attention 图 2 的左右两幅）。
_CAPTION_COLUMN_MAX_GAP = 80.0
_LABEL_GROW_LIMIT = 20.0  # 图外文字最多向外扩这么远
_LABEL_GROW_GAP = 12.0  # 一行文字离图（或已并入的文字）这么近才算图的标注
_LABEL_MAX_CHARS = 60  # 图外的长行是正文，不并入
_LOGO_MIN_PAGES = 3
_SCAN_MAX_TEXT_CHARS = 30
_SCAN_MIN_IMAGE_RATIO = 0.6

# 渲染上限：识图时按位置现渲染；超大区域按比例降采样，避免一张图上传几 MB。
DEFAULT_RENDER_DPI = 150
DEFAULT_RENDER_MAX_PIXELS = 1_600_000
# PNG 超过这么大（多为照片、热力图）就改用 JPEG 上传。
_PNG_MAX_BYTES = 600_000
_JPEG_QUALITY = 85


@dataclass
class FigureScan:
    """一份 PDF 的找图结果。

    `masked_lines` 是并入各图 `inner_text` 的文字行位置（按页），正文清洗据此把它们从正文删去；
    图区域（`regions_by_page`）用来删掉落在图里的矢量线条。
    """

    figures: list[DocumentFigure] = field(default_factory=list)
    scanned_pages: list[int] = field(default_factory=list)
    masked_lines: dict[int, list[BBox]] = field(default_factory=dict)
    failed_pages: list[int] = field(default_factory=list)
    seconds: float = 0.0

    def regions_by_page(self) -> dict[int, list[BBox]]:
        regions: dict[int, list[BBox]] = {}
        for fig in self.figures:
            regions.setdefault(fig.page, []).append(fig.bbox)
        return regions


@dataclass
class _Line:
    rect: Any  # pymupdf.Rect
    text: str
    block: int
    size: float  # 该行最大字号


@dataclass
class _Candidate:
    rect: Any
    kind: str


def _gap(a: Any, b: Any) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return float((dx * dx + dy * dy) ** 0.5)


def _caption_distance(cand: Any, caption: Any, *, figure: bool) -> float:
    """候选到标题的距离；图标题在候选上方时加罚（图注惯例在下）。"""
    gap = _gap(cand, caption)
    if figure and caption.y1 <= cand.y0 + 1:
        gap += _CAPTION_ABOVE_PENALTY
    return gap


def _area(rect: Any) -> float:
    return max(0.0, rect.width) * max(0.0, rect.height)


def _page_lines(page: Any, rect_cls: Any) -> tuple[list[_Line], list[tuple[Any, str]]]:
    """页内文字行 + 文字块（块的整段文字用于判断标题）。"""
    lines: list[_Line] = []
    blocks: list[tuple[Any, str]] = []
    for b_index, block in enumerate(page.get_text("dict").get("blocks", [])):
        if block.get("type") != 0:
            continue
        texts: list[str] = []
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            size = max((float(span.get("size", 0.0)) for span in spans), default=0.0)
            lines.append(_Line(rect_cls(line["bbox"]), text, b_index, size))
            texts.append(text)
        if texts:
            blocks.append((rect_cls(block["bbox"]), " ".join(texts)))
    return lines, blocks


def _body_blocks(lines: list[_Line], rect_cls: Any) -> list[Any]:
    """正文段落块：字号等于本页正文字号、至少两行、平均行长够长。

    图里也会有长句（ToT 的提示词示例），但字号通常小于正文；这里认出的块绝不当作图内文字，
    还用来把误连了正文的绘图簇切开。
    """
    weights: dict[float, int] = {}
    for line in lines:
        key = round(line.size * 2) / 2
        weights[key] = weights.get(key, 0) + len(line.text)
    if not weights:
        return []
    body_size = max(weights, key=lambda k: weights[k])
    by_block: dict[int, list[_Line]] = {}
    for line in lines:
        by_block.setdefault(line.block, []).append(line)
    found: list[Any] = []
    for members in by_block.values():
        if len(members) < _BODY_MIN_LINES:
            continue
        same_size = sum(abs(m.size - body_size) <= _BODY_SIZE_TOLERANCE for m in members)
        if same_size < 0.8 * len(members):
            continue
        if sum(len(m.text) for m in members) / len(members) < _BODY_MIN_AVG_CHARS:
            continue
        rect = rect_cls(members[0].rect)
        for m in members[1:]:
            rect |= m.rect
        found.append(rect)
    return found


def _split_by_body(cand: _Candidate, body: list[Any], rect_cls: Any) -> _Candidate | None:
    """绘图簇里夹着正文段落时（簇误连了上下两块），只保留正文之外最大的那一段。"""
    hits = [b for b in body if b.intersects(cand.rect)]
    if not hits:
        return cand
    r = cand.rect
    cuts = sorted((max(r.y0, b.y0), min(r.y1, b.y1)) for b in hits)
    pieces: list[Any] = []
    top = r.y0
    for y0, y1 in cuts:
        if y0 > top:
            pieces.append(rect_cls(r.x0, top, r.x1, y0))
        top = max(top, y1)
    if top < r.y1:
        pieces.append(rect_cls(r.x0, top, r.x1, r.y1))
    pieces = [p for p in pieces if p.height >= _MIN_VECTOR_SIDE]
    if not pieces:
        return None
    return _Candidate(max(pieces, key=_area), cand.kind)


def _logo_digests(doc: Any) -> set[bytes]:
    """跨 ≥3 页出现的同一位图（期刊 logo、页眉图标）不是图。"""
    pages_by_digest: dict[bytes, set[int]] = {}
    for page in doc:
        for info in page.get_image_info(hashes=True):
            digest = info.get("digest")
            if digest:
                pages_by_digest.setdefault(digest, set()).add(page.number)
    return {d for d, pages in pages_by_digest.items() if len(pages) >= _LOGO_MIN_PAGES}


def _is_scanned_page(page: Any, page_area: float, rect_cls: Any) -> bool:
    if len(page.get_text("text").strip()) > _SCAN_MAX_TEXT_CHARS:
        return False
    for info in page.get_image_info():
        rect = rect_cls(info["bbox"]) & page.rect
        if not rect.is_empty and _area(rect) >= page_area * _SCAN_MIN_IMAGE_RATIO:
            return True
    return False


def _candidates(page: Any, page_area: float, logos: set[bytes], rect_cls: Any) -> list[_Candidate]:
    found: list[_Candidate] = []
    for info in page.get_image_info(hashes=True):
        if info.get("digest") in logos:
            continue
        rect = rect_cls(info["bbox"]) & page.rect
        if rect.is_empty or _area(rect) < page_area * _MIN_RASTER_AREA_RATIO:
            continue
        if min(rect.width, rect.height) < _MIN_RASTER_SIDE:
            continue
        found.append(_Candidate(rect, KIND_RASTER))
    for rect in page.cluster_drawings(x_tolerance=3, y_tolerance=3):
        rect = rect & page.rect
        if rect.is_empty or _area(rect) < page_area * _MIN_VECTOR_AREA_RATIO:
            continue
        if min(rect.width, rect.height) < _MIN_VECTOR_SIDE:
            continue
        found.append(_Candidate(rect, KIND_VECTOR))
    return found


def _cut_table(cand: _Candidate, table: Any, rect_cls: Any) -> _Candidate | None:
    """从候选里切掉一张真表格：几乎重合就丢弃，否则保留表格之外较大的一侧。"""
    overlap = cand.rect & table
    if overlap.is_empty:
        return cand
    if _area(overlap) >= 0.8 * _area(cand.rect):
        return None
    r = cand.rect
    pieces = [
        rect_cls(r.x0, r.y0, r.x1, table.y0),
        rect_cls(r.x0, table.y1, r.x1, r.y1),
        rect_cls(r.x0, r.y0, table.x0, r.y1),
        rect_cls(table.x1, r.y0, r.x1, r.y1),
    ]
    best = max(pieces, key=_area)
    if best.is_empty or _area(best) < 0.15 * _area(r):
        return None
    return _Candidate(best, cand.kind)


def _real_tables(page: Any, table_captions: list[Any], rect_cls: Any) -> list[Any]:
    """带表标题、且单元格大多有字的线框表。

    图表的网格线也会被 find_tables 认成表格（ResNet 图 4），但那些「单元格」几乎是空的。
    """
    if not table_captions:
        return []
    try:
        tables = page.find_tables(strategy="lines_strict").tables
    except Exception:  # find_tables 对个别畸形页会抛错：当作没有表格，不影响找图
        return []
    real: list[Any] = []
    for table in tables:
        rect = rect_cls(table.bbox)
        if min(_gap(rect, c) for c in table_captions) > _CAPTION_MAX_GAP:
            continue
        try:
            cells = [cell for row in table.extract() for cell in row]
        except Exception:
            continue
        filled = sum(1 for cell in cells if cell and str(cell).strip())
        if cells and filled / len(cells) >= _TABLE_MIN_FILLED_RATIO:
            real.append(rect)
    return real


def _grow_with_labels(
    region: Any, lines: list[_Line], blocked: list[Any], rect_cls: Any
) -> tuple[Any, list[_Line]]:
    """并入图内文字与紧贴图外的短标注行，返回扩大后的区域与这些行。"""
    limit = rect_cls(
        region.x0 - _LABEL_GROW_LIMIT, region.y0 - _LABEL_GROW_LIMIT,
        region.x1 + _LABEL_GROW_LIMIT, region.y1 + _LABEL_GROW_LIMIT,
    )
    grown = rect_cls(region)
    taken: list[_Line] = []
    taken_ids: set[int] = set()

    def available(line: _Line) -> bool:
        return id(line) not in taken_ids and not any(line.rect.intersects(b) for b in blocked)

    for line in lines:  # 图内（中心落在原区域里）的行，无论长短都是图的一部分
        center = (line.rect.tl + line.rect.br) / 2
        if available(line) and region.contains(center):
            taken.append(line)
            taken_ids.add(id(line))
    for _ in range(3):
        changed = False
        for line in lines:
            if not available(line) or len(line.text) > _LABEL_MAX_CHARS:
                continue
            if not limit.contains(line.rect) or _gap(line.rect, grown) > _LABEL_GROW_GAP:
                continue
            grown |= line.rect
            taken.append(line)
            taken_ids.add(id(line))
            changed = True
        if not changed:
            break
    for line in taken:
        grown |= line.rect
    taken.sort(key=lambda ln: (round(ln.rect.y0, 0), ln.rect.x0))
    return grown, taken


def _page_figures(
    page: Any, page_no: int, doc_id: str, logos: set[bytes], rect_cls: Any
) -> tuple[list[DocumentFigure], list[BBox]]:
    """本页的图，以及并入图内文字的文字行位置。"""
    page_area = _area(page.rect)
    candidates = _candidates(page, page_area, logos, rect_cls)
    if not candidates:
        return [], []
    lines, blocks = _page_lines(page, rect_cls)
    body = _body_blocks(lines, rect_cls)
    candidates = [c for c in (_split_by_body(c, body, rect_cls) for c in candidates) if c]
    fig_caps = [(r, t) for r, t in blocks if FIGURE_CAPTION_RE.match(t)]
    tab_caps = [r for r, t in blocks if TABLE_CAPTION_RE.match(t)]
    tables = _real_tables(page, tab_caps, rect_cls)
    if tables:
        cut: list[_Candidate] = []
        for cand in candidates:
            current: _Candidate | None = cand
            for table in tables:
                current = _cut_table(current, table, rect_cls) if current else None
            if current is not None:
                cut.append(current)
        candidates = cut

    grouped: dict[int, list[_Candidate]] = {}  # 图标题下标 → 候选
    loose: list[_Candidate] = []
    for cand in candidates:
        nearest: tuple[float, str, int] | None = None
        for idx, (rect, _) in enumerate(fig_caps):
            dist = _caption_distance(cand.rect, rect, figure=True)
            if _gap(cand.rect, rect) <= _CAPTION_MAX_GAP and (nearest is None or dist < nearest[0]):
                nearest = (dist, "fig", idx)
        for rect in tab_caps:
            dist = _caption_distance(cand.rect, rect, figure=False)
            if dist <= _CAPTION_MAX_GAP and (nearest is None or dist < nearest[0]):
                nearest = (dist, "tab", -1)
        if nearest is None:
            loose.append(cand)
        elif nearest[1] == "fig":
            grouped.setdefault(nearest[2], []).append(cand)
    # 未配上标题的候选若紧挨某张已归属的图，视为同一张图的一部分。
    changed = True
    while changed and loose:
        changed = False
        for cand in list(loose):
            for members in grouped.values():
                if any(_gap(cand.rect, m.rect) <= _SIBLING_MAX_GAP for m in members):
                    members.append(cand)
                    loose.remove(cand)
                    changed = True
                    break
    for cand in list(loose):
        for idx, (cap, _) in enumerate(fig_caps):
            if (cand.rect.y1 <= cap.y0 + 2 and cap.y0 - cand.rect.y1 <= _CAPTION_COLUMN_MAX_GAP
                    and cand.rect.x0 >= cap.x0 - 10 and cand.rect.x1 <= cap.x1 + 10
                    and idx in grouped):
                grouped[idx].append(cand)
                loose.remove(cand)
                break
    uncaptioned = [
        cand for cand in loose
        if cand.kind == KIND_RASTER
        and _area(cand.rect) >= page_area * _UNCAPTIONED_RASTER_AREA_RATIO
    ]

    blocked = [r for r, _ in fig_caps] + [rect_cls(r) for r in tab_caps] + tables + body
    raw: list[tuple[Any, str, str, str]] = []  # (区域, 类型, 标签, 标题)
    for cap_idx, cands in grouped.items():
        rect = rect_cls(cands[0].rect)
        for cand in cands[1:]:
            rect |= cand.rect
        kinds = {c.kind for c in cands}
        kind = kinds.pop() if len(kinds) == 1 else KIND_MIXED
        cap_rect, cap_text = fig_caps[cap_idx]
        match = FIGURE_CAPTION_RE.match(cap_text)
        label = re.sub(r"\s+", " ", match.group("label")).strip() if match else ""
        raw.append((rect, kind, label, cap_text))
    raw.extend((cand.rect, cand.kind, "", "") for cand in uncaptioned)

    figures: list[DocumentFigure] = []
    masked: list[BBox] = []
    claimed: list[Any] = []
    for rect, kind, label, caption in sorted(raw, key=lambda r: (r[0].y0, r[0].x0)):
        grown, taken = _grow_with_labels(rect, lines, blocked + claimed, rect_cls)
        claimed.append(grown)
        masked.extend((ln.rect.x0, ln.rect.y0, ln.rect.x1, ln.rect.y1) for ln in taken)
        ordinal = len(figures)
        figures.append(DocumentFigure(
            figure_id=figure_id_for(doc_id, page_no, ordinal),
            doc_id=doc_id,
            page=page_no,
            ordinal=ordinal,
            bbox=(round(grown.x0, 2), round(grown.y0, 2), round(grown.x1, 2), round(grown.y1, 2)),
            kind=kind,
            label=label,
            caption=re.sub(r"\s+", " ", caption).strip(),
            inner_text="\n".join(line.text for line in taken),
        ))
    return figures, masked


def detect_figures(doc: Any, doc_id: str) -> FigureScan:
    """对已打开的 pymupdf.Document 找图。不改动文档；出错的页记入 failed_pages 后继续。"""
    import pymupdf

    started = time.perf_counter()
    scan = FigureScan()
    logos = _logo_digests(doc)
    for page in doc:
        page_no = page.number + 1
        page_area = _area(page.rect)
        if page_area <= 0:
            continue
        if _is_scanned_page(page, page_area, pymupdf.Rect):
            scan.scanned_pages.append(page_no)
            continue
        try:
            figures, masked = _page_figures(page, page_no, doc_id, logos, pymupdf.Rect)
        except Exception:  # 个别畸形页不能拖垮整篇：这一页不找图，正文照常
            scan.failed_pages.append(page_no)
            continue
        scan.figures.extend(figures)
        if masked:
            scan.masked_lines[page_no] = masked
    scan.seconds = time.perf_counter() - started
    return scan


def detect_figures_in_file(pdf_path: str, doc_id: str) -> FigureScan:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return detect_figures(doc, doc_id)


def render_figure_png(
    pdf_path: str,
    page: int,
    bbox: BBox,
    *,
    dpi: int = DEFAULT_RENDER_DPI,
    max_pixels: int = DEFAULT_RENDER_MAX_PIXELS,
) -> bytes:
    """按位置把一张图渲染成 PNG（识图时现渲染，不落盘）。超过像素上限时等比降低分辨率。"""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        pdf_page = doc[page - 1]
        clip = pymupdf.Rect(bbox) & pdf_page.rect
        if clip.is_empty:
            raise ValueError(f"Figure region is outside page {page}")
        zoom = dpi / 72.0
        pixels = clip.width * zoom * clip.height * zoom
        if pixels > max_pixels:
            zoom *= (max_pixels / pixels) ** 0.5
        pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        return bytes(pix.tobytes("png"))


def render_figure_image(
    pdf_path: str, page: int, bbox: BBox, *, dpi: int = DEFAULT_RENDER_DPI,
    max_pixels: int = DEFAULT_RENDER_MAX_PIXELS,
) -> tuple[bytes, str]:
    """识图用：先出 PNG；照片类的图 PNG 过大时改用 JPEG（像素不变，识图 token 不变，上传小得多）。

    返回 (图片字节, MIME 类型)。
    """
    png = render_figure_png(pdf_path, page, bbox, dpi=dpi, max_pixels=max_pixels)
    if len(png) <= _PNG_MAX_BYTES:
        return png, "image/png"
    import pymupdf

    pix = pymupdf.Pixmap(png)
    return bytes(pix.tobytes("jpg", jpg_quality=_JPEG_QUALITY)), "image/jpeg"


__all__ = [
    "DEFAULT_RENDER_DPI",
    "DEFAULT_RENDER_MAX_PIXELS",
    "FIGURE_CAPTION_RE",
    "FigureScan",
    "TABLE_CAPTION_RE",
    "detect_figures",
    "detect_figures_in_file",
    "render_figure_image",
    "render_figure_png",
]
