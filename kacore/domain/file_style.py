"""入库文件的命名与目录风格（domain 层·纯函数，零依赖）。

为何存在：3.0.0 起外部文献可以入库，并可推送到 Zotero（文件放进 ZotMoov 目录）。新文件要和用户
已有的文件长得一样：用户的 Zotero 附件多为独立 PDF，书目只写在文件名里，形如
`Wei et al. (2026) - Beyond Static Personas, Situational Personality Steering….pdf`，
放在与 Zotero 集合同名的文件夹里（2026-11-19 在用户库上统计：170 个附件中 124 个是这个格式）。

本模块做两件事：
- `infer_style`：统计一批现有文件名用的是哪种格式（复用 `filename_biblio` 的两种格式定义），
  只认格式，不判断内容含义；
- `render_stem` / `render_dir`：按风格生成新文件名与目录，并处理 Windows 不允许的字符。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

from kacore.domain.filename_biblio import _DEDUP_PREFIX_RE, _PATTERN_DASH, _PATTERN_PAREN

TEMPLATE_PAREN = "{authors} ({year}) - {title}"
TEMPLATE_DASH = "{authors} - {year} - {title}"
DIR_TEMPLATE_COLLECTION = "{collection}"
NO_YEAR = "n.d."
NO_AUTHOR = "Anon."
# 文件名主干的长度上限。Windows 路径默认上限 260 字符；用户的 ZotMoov 根目录约 45 字符、
# 集合路径常见 30–60 字符，留出余量后取 150（假设值，可在 FileStyle 里改）。
DEFAULT_MAX_STEM = 150
_WINDOWS_FORBIDDEN = '<>"\\|?*'
_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                             *(f"LPT{i}" for i in range(1, 10))})


# ── 风格 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileStyle:
    """命名风格：name_template 支持 {authors} {year} {title}；dir_template 支持 {collection}。"""

    name_template: str = TEMPLATE_PAREN
    dir_template: str = DIR_TEMPLATE_COLLECTION
    max_stem: int = DEFAULT_MAX_STEM

    def __post_init__(self) -> None:
        if "{title}" not in self.name_template:
            raise ValueError("name_template must contain {title}")


def render_authors(surnames: Sequence[str]) -> str:
    """1 人写姓；2 人写 `A and B`；3 人及以上写 `A et al.`（与用户现有文件一致）。"""
    names = [s.strip() for s in surnames if s and s.strip()]
    if not names:
        return NO_AUTHOR
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} et al."


def _clean_chars(text: str) -> str:
    """去掉 Windows 不允许的字符：冒号写成逗号（用户的写法），斜杠写成短横线，空白合并。"""
    out = text.replace(": ", ", ").replace(":", ",").replace("/", "-")
    out = "".join(ch for ch in out if ch not in _WINDOWS_FORBIDDEN and ord(ch) >= 32)
    return " ".join(out.split())


def safe_segment(text: str) -> str:
    """一整段文件名 / 目录名：清理字符，去掉结尾的空格和点（Windows 不允许），避开保留名。"""
    out = _clean_chars(text).rstrip(" .")
    if out.upper() in _RESERVED_NAMES:
        out = f"_{out}"
    return out


def render_stem(style: FileStyle, surnames: Sequence[str], year: str, title: str) -> str:
    """生成文件名主干（不含扩展名）；超过 max_stem 时只截标题，作者与年份保持完整。"""
    authors = _clean_chars(render_authors(surnames))      # 保留 et al. 的点
    year_text = _clean_chars(year.strip()) or NO_YEAR
    clean_title = safe_segment(title) or "Untitled"
    before, after = style.name_template.split("{title}", 1)
    head = before.format(authors=authors, year=year_text)
    tail = after.format(authors=authors, year=year_text)
    room = max(style.max_stem - len(head) - len(tail), 20)
    if len(clean_title) > room:
        clean_title = clean_title[:room].rstrip(" ,.-")
    return safe_segment(head + clean_title + tail)


def render_dir(style: FileStyle, collection_path: Sequence[str]) -> PurePath:
    """目录：按集合路径逐级生成（每级单独清理字符）；没有集合时返回空路径（放在根目录）。"""
    if "{collection}" not in style.dir_template or not collection_path:
        return PurePath()
    parts = [safe_segment(p) for p in collection_path if p and p.strip()]
    return PurePath(*[p for p in parts if p])


def unique_name(stem: str, suffix: str, taken: set[str]) -> str:
    """同名时加序号 `stem 1.pdf`、`stem 2.pdf`（与 ZotMoov 的做法一致）；taken 用小写比较。"""
    candidate = f"{stem}{suffix}"
    n = 1
    while candidate.lower() in taken:
        candidate = f"{stem} {n}{suffix}"
        n += 1
    return candidate


# ── 从现有文件名统计风格 ────────────────────────────────────


@dataclass
class StyleReport:
    """一批现有文件名的格式统计。chosen 是据此采用的风格。"""

    total: int = 0
    paren: int = 0
    dash: int = 0
    other: int = 0
    authors_one: int = 0
    authors_two: int = 0
    authors_et_al: int = 0
    chosen: FileStyle = field(default_factory=FileStyle)


def infer_style(filenames: Iterable[str]) -> StyleReport:
    """统计格式并选出风格：括号年份式多于短横线式时用前者，否则用后者；都没有时用默认（括号年份式）。

    只按格式分类（`filename_biblio` 的两个格式定义），不看标题内容。
    """
    report = StyleReport()
    for name in filenames:
        stem = _DEDUP_PREFIX_RE.sub("", PurePath(name).stem).strip()
        if not stem:
            continue
        report.total += 1
        match = _PATTERN_PAREN.match(stem)
        if match:
            report.paren += 1
            _count_authors(report, match.group("authors"))
        elif _PATTERN_DASH.match(stem):
            report.dash += 1
        else:
            report.other += 1
    template = TEMPLATE_DASH if report.dash > report.paren else TEMPLATE_PAREN
    report.chosen = FileStyle(name_template=template)
    return report


def _count_authors(report: StyleReport, authors: str) -> None:
    if "et al" in authors:
        report.authors_et_al += 1
    elif " and " in authors or "&" in authors:
        report.authors_two += 1
    else:
        report.authors_one += 1


__all__ = [
    "DEFAULT_MAX_STEM",
    "DIR_TEMPLATE_COLLECTION",
    "NO_AUTHOR",
    "NO_YEAR",
    "TEMPLATE_DASH",
    "TEMPLATE_PAREN",
    "FileStyle",
    "StyleReport",
    "infer_style",
    "render_authors",
    "render_dir",
    "render_stem",
    "safe_segment",
    "unique_name",
]
