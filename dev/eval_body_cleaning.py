"""正文去图表污染的评测（L3，开发期脚本，不进安装包）。

用法：python dev/eval_body_cleaning.py path/to/a.pdf path/to/b.pdf ...

对每份 PDF 比较「清洗前」（cleaning-v3：不找图、不做 L1）与「清洗后」
（当前 extract_pdf_markdown + 找图遮罩）：
  - 伪表格数（表头为自动生成的 ColN）、`<br>` 数、乱码行数；
  - 守恒检查：清洗后正文少了的词，必须出现在某张图的 inner_text 里，或出自被判为垃圾的
    伪表格 / 乱码行 / 刻度。两边都找不到的词列为「疑似误删」，需要人工复核。
只读：不写数据库、不改 PDF。
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kacore.managers import markdown_extractor  # noqa: E402
from kacore.managers.figure_detection import detect_figures_in_file  # noqa: E402
from kacore.managers.figure_noise import is_mojibake, strip_figure_noise  # noqa: E402
from kacore.managers.markdown_extractor import extract_pdf_markdown  # noqa: E402

_WORD = re.compile(r"[A-Za-z]{4,}")
_TABLE = re.compile(r"(?:^\|.*\|\n)+", re.M)


def _v3_markdown(path: str) -> str:
    """旧清洗（v3）：同一条清洗路径，只是去掉 L1 规则、不给找图结果。"""
    original = markdown_extractor.strip_figure_noise
    markdown_extractor.strip_figure_noise = lambda text, preserve_offsets=False: text
    try:
        return extract_pdf_markdown(path).clean_markdown
    finally:
        markdown_extractor.strip_figure_noise = original


def _stats(text: str) -> dict[str, int]:
    tables = _TABLE.findall(text)
    return {
        "chars": len(text),
        "pseudo_tables": sum(1 for t in tables if "|Col1|" in t.split("\n")[0]),
        "br": text.count("<br>"),
        "mojibake_lines": sum(1 for line in text.split("\n") if is_mojibake(line)),
    }


def evaluate(path: str) -> dict[str, object]:
    raw = _v3_markdown(path)
    started = time.perf_counter()
    scan = detect_figures_in_file(path, Path(path).stem)
    artifact = extract_pdf_markdown(path, figure_scan=scan)
    seconds = time.perf_counter() - started
    garbage = Counter(_WORD.findall(raw)) - Counter(_WORD.findall(strip_figure_noise(raw)))
    moved = Counter(_WORD.findall("\n".join(f.inner_text for f in scan.figures)))
    lost = Counter(_WORD.findall(raw)) - Counter(_WORD.findall(artifact.clean_markdown))
    unexplained = lost - moved - garbage
    return {
        "file": Path(path).name,
        "figures": len(scan.figures),
        "scanned_pages": scan.scanned_pages,
        "seconds": round(seconds, 2),
        "before": _stats(raw),
        "after": _stats(artifact.clean_markdown),
        "lost_words": sum(lost.values()),
        "unexplained": sum(unexplained.values()),
        "unexplained_sample": unexplained.most_common(15),
    }


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    for path in paths:
        result = evaluate(path)
        print(f"== {result['file']}  figures={result['figures']}  {result['seconds']}s  "
              f"scanned={result['scanned_pages']}")
        print(f"   before {result['before']}")
        print(f"   after  {result['after']}")
        print(f"   lost words {result['lost_words']}, unexplained {result['unexplained']}: "
              f"{result['unexplained_sample']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
