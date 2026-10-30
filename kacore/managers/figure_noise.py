"""正文去图表污染（managers 层）：L1 文本规则 + L2 按图位置遮罩。

为什么存在：PyMuPDF4LLM 把矢量图表误判成表格、把图里的刻度与标注拆成碎行写进正文，
图里用了自定义编码字体时还会抽出整行乱码。这些文字进了 chunk，既污染检索又浪费 token。

- L1（只看文字，不需要原 PDF，旧文档可从 provenance 的原始页重洗）：
  · 伪表格：表头是自动生成的 `|Col1|Col2|…|`，且多半是空格子、删除线、`<br>` 堆叠或乱码；
  · 乱码行：字母大多落在拉丁扩展 / IPA / 修饰符区段（如 `GĮŔũƜ`），整行删除；
  · 孤立刻度：连续 3 行以上只有一个短数字（坐标轴刻度）。
  真表格（表头不是自动生成的、内容正常）一律保留。
- L2（需要原 PDF，上传与重抽取时）：找图得到每张图的「图内文字行」位置，抽正文前在内存副本上
  删掉这些行的文字，并删掉完全落在图区域里的矢量线条（伪表格的来源）。原 PDF 不改。
  这些文字没有丢：它们存在图空位的 `inner_text` 里，进图 chunk。
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kacore.managers.figure_detection import FigureScan

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_SYNTHETIC_HEADER_RE = re.compile(r"^\s*\|(?:[^|]*\|)*\s*Col\d+\s*\|")
_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_TICK_RE = re.compile(r"^\s*[-+−]?\d{1,4}(?:[.,]\d{1,3})?\s*%?\s*(?:\[\d+\])?\s*$")
_MIN_TICK_RUN = 3
_PSEUDO_EMPTY_RATIO = 0.5
_PSEUDO_BR_RATIO = 0.3
_MOJIBAKE_RATIO = 0.4
_MOJIBAKE_MIN_LETTERS = 3

# 抽取乱码常落的区段：拉丁扩展-A/B、IPA、空格修饰符。正常的欧洲语言里这些字母占比远低于 40%。
_MOJIBAKE_RANGES = ((0x0100, 0x024F), (0x0250, 0x02AF), (0x02B0, 0x02FF))


def _is_suspicious(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _MOJIBAKE_RANGES)


def is_mojibake(text: str) -> bool:
    """一段文字的字母（含修饰符）里，可疑区段占比 ≥ 40% 视为乱码。"""
    letters = [
        ch for ch in text
        if ch.isalpha() or unicodedata.category(ch) in {"Lm", "Sk"}
    ]
    if len(letters) < _MOJIBAKE_MIN_LETTERS:
        return False
    return sum(_is_suspicious(ch) for ch in letters) / len(letters) >= _MOJIBAKE_RATIO


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def is_pseudo_table(rows: list[str]) -> bool:
    """markdown 表格（含表头与分隔行）是否是图表误判出来的伪表格。"""
    if not rows:
        return False
    if is_mojibake(" ".join(rows)):
        return True
    if not _SYNTHETIC_HEADER_RE.match(rows[0]):
        return False
    body = [r for r in rows[1:] if not _SEPARATOR_RE.match(r)]
    cells = [c for r in body for c in _cells(r)]
    if not cells:
        return True
    empty = sum(1 for c in cells if not c or set(c) <= {"~", "-", " "})
    if empty / len(cells) >= _PSEUDO_EMPTY_RATIO:
        return True
    if "~~" in "".join(cells):
        return True
    return sum(1 for c in cells if "<br>" in c) / len(cells) >= _PSEUDO_BR_RATIO


def _drop_tick_runs(lines: list[str], blank: bool) -> list[str]:
    """删除连续 ≥3 个只含短数字的行（中间的空行不打断连续性）；blank 时以等长空格代替。"""
    keep = [True] * len(lines)
    run: list[int] = []

    def flush() -> None:
        if len(run) >= _MIN_TICK_RUN:
            for idx in run:
                keep[idx] = False
        run.clear()

    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        if _TICK_RE.match(line):
            run.append(idx)
        else:
            flush()
    flush()
    if blank:
        return [line if keep[idx] else " " * len(line) for idx, line in enumerate(lines)]
    return [line for idx, line in enumerate(lines) if keep[idx]]


def strip_figure_noise(text: str, *, preserve_offsets: bool = False) -> str:
    """L1：从一页原始 Markdown 里删去伪表格、乱码行与孤立刻度。幂等。

    preserve_offsets=True 时删去的行换成等长空格（不改变任何字符的位置），
    供脚注偏移仍需指向原始页的抽取路径使用。
    """
    lines = text.split("\n")
    out: list[str] = []

    def drop(dropped: list[str]) -> None:
        if preserve_offsets:
            out.extend(" " * len(line) for line in dropped)

    idx = 0
    while idx < len(lines):
        if _TABLE_ROW_RE.match(lines[idx]):
            end = idx
            while end < len(lines) and _TABLE_ROW_RE.match(lines[end]):
                end += 1
            table = lines[idx:end]
            if is_pseudo_table(table):
                drop(table)
            else:
                out.extend(table)
            idx = end
            continue
        line = lines[idx]
        if is_mojibake(line):
            drop([line])
        else:
            out.append(line)
        idx += 1
    return "\n".join(_drop_tick_runs(out, preserve_offsets))


def mask_figure_text(doc: Any, scan: FigureScan) -> int:
    """L2：在已打开的（内存）文档上删掉图内文字行与图区域里的矢量线条，返回处理的页数。

    文字只按逐行位置删（不按整个图区域删），图标题与旁边的正文因此不受影响；
    线条只删完全落在图区域里的，页眉线、表格线不会被误删。
    """
    import pymupdf

    masked_pages = 0
    regions = scan.regions_by_page()
    for page_no in sorted(set(regions) | set(scan.masked_lines)):
        page = doc[page_no - 1]
        lines = scan.masked_lines.get(page_no, [])
        for x0, y0, x1, y1 in lines:
            # 竖向内缩一点，免得行距很紧时擦到上下相邻行的字。
            page.add_redact_annot(pymupdf.Rect(x0, y0 + 0.5, x1, y1 - 0.5))
        if lines:
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        for bbox in regions.get(page_no, []):
            page.add_redact_annot(pymupdf.Rect(bbox))
        if regions.get(page_no):
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_NONE,
            )
        masked_pages += 1
    return masked_pages


__all__ = ["is_mojibake", "is_pseudo_table", "mask_figure_text", "strip_figure_noise"]
