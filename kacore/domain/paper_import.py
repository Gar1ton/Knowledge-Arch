"""外部论文入库的数据类型与纯函数（domain 层·零依赖）。

为何存在：3.0.0 起回答里引用的外部论文可以入库（来源栏「入库」）。入库要回答三件事，都在这里用
纯函数回答，便于单测：
- 文件叫什么：按设置项里的模板生成（默认是用户现有附件的写法 `{authors} ({year}) - {title}`，
  用户 11-23 选「设置项」而不是每次统计文件名）；作者姓氏只按「姓, 名」的逗号切，沿用
  `citation.normalize_creators`，不按空格猜。
- 去哪里找 PDF：只看这次运行账本里已经记下的链接（用户 11-23 选「不再调用模型」）。只认格式
  （http(s):// 开头的串），不判断链接指向什么；是不是 PDF 由下载时的 `%PDF-` 文件头决定。
- 找不到 PDF 时给用户哪些能在浏览器打开的链接（用户 11-23 评论：提供 DOI 链接等让用户自己下载）。

记录本身（`ImportRecord`）存在迁移 030 的 `paper_imports` 表里；出处（哪次运行、哪个回答、哪次调用）
也存在那里，文档的书目字段则照常写进文档的 local_meta，用户之后照常可以改。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from kacore.domain.citation import ANON, NO_DATE, normalize_creators
from kacore.domain.tool_calls import CITE_TOOL, Citation, ToolCall, normalize_text

DEFAULT_TEMPLATE = "{authors} ({year}) - {title}"
# 入库时一篇最多试几个链接（假设值：账本里同一篇通常只出现落地页、索引页与开放获取 PDF 几个链接）。
MAX_PDF_TRIES = 6
# 文件名主干的最大字符数（假设值：给 ZotMoov 的多层集合目录留出 Windows 260 字符路径的余量）。
MAX_STEM_CHARS = 150

IMPORT_PENDING_PDF = "pending_pdf"
IMPORT_IMPORTED = "imported"
IMPORT_DISMISSED = "dismissed"
IMPORT_STATUSES = (IMPORT_PENDING_PDF, IMPORT_IMPORTED, IMPORT_DISMISSED)

PUSH_PUSHED = "pushed"
PUSH_CLAIMED = "claimed"
PUSH_FAILED = "failed"

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                 "http://dx.doi.org/", "doi:")
_URL_RE = re.compile(r"https?://[^\s<>\"'\[\]{}|\\^`]+")
_URL_TAIL = ".,;:)'\""
# Windows 文件名不允许的字符：冒号写成逗号（用户现有文件「A: B」都写成「A, B」），
# 其余去掉或换成短横。
_WINDOWS_MAP = str.maketrans({":": ",", "?": "", "*": "", '"': "'", "<": "", ">": "",
                              "|": "-", "/": "-", "\\": "-"})
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                               *(f"LPT{i}" for i in range(1, 10))})


@dataclass
class ImportRecord:
    """一次入库。target 是目标集合的 coll_key；source 是出处（对话、回答序号、问题、工具、调用、
    位置），入库时从账本抄下来，账本以后有变化也不影响；tried 是试过的链接与失败原因。"""

    import_id: str
    capture_id: str
    ord: int
    fields: dict[str, Any]
    filename: str
    target: str
    status: str = IMPORT_PENDING_PDF
    doc_id: str = ""
    pdf_url: str = ""
    pdf_sha256: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    tried: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PushRecord:
    """一篇本地文档推送到 Zotero（迁移 030 的 zotero_pushes）。

    推送 = 移交：文件移进 ZotMoov 的目录，Zotero 里建一条独立 linked_file 附件（attachment_key）；
    下次拉取按 attachment_key 认领原文档（status 从 pushed 变 claimed），不按新编号重建。
    失败时 message 写原因，文件已移回原处。"""

    doc_id: str
    library_id: str
    collection_key: str
    write_token: str
    status: str = PUSH_PUSHED
    attachment_key: str = ""
    path: str = ""
    file_path: str = ""
    message: str = ""
    pushed_at: str = ""
    claimed_at: str | None = None


# ── 标识 ────────────────────────────────────────────────────


def doi_key(text: str) -> str:
    """DOI 比较用的格式规整：去解析前缀、小写。不是 10. 开头的返回空串。"""
    value = (text or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value if value.startswith("10.") else ""


# ── 文件名 ──────────────────────────────────────────────────


def _authors(surnames: Sequence[str]) -> str:
    """用户现有文件名的写法：1 位 → 姓；2 位 → 「A and B」；3 位及以上 → 「A et al.」。"""
    if not surnames:
        return ANON
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} and {surnames[1]}"
    return f"{surnames[0]} et al."


def _year(fields: Mapping[str, Any]) -> str:
    text = str(fields.get("year") or "").strip()
    return text[:4] if text[:4].isdigit() else NO_DATE


class _Blank(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def windows_safe(text: str, *, max_chars: int = MAX_STEM_CHARS, whole_name: bool = False) -> str:
    """文件名主干 / 文件夹名在 Windows / macOS / Linux 上都能用：去控制字符、替换不允许的字符、
    合并空白、避开保留名、截到 max_chars。whole_name=True（完整的文件夹名）时再去掉结尾的点——
    Windows 不允许名字以点结尾；文件名主干后面还要接 .pdf，「et al.」的点要留着。"""
    cleaned = "".join(ch for ch in text if ch >= " ").translate(_WINDOWS_MAP)
    cleaned = " ".join(cleaned.split()).replace(" ,", ",")
    cleaned = cleaned[:max_chars].rstrip(". " if whole_name else " ")
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or "Untitled"


def paper_filename(fields: Mapping[str, Any], template: str = DEFAULT_TEMPLATE) -> str:
    """按模板生成 PDF 文件名。模板可用 {authors} {year} {title}；写错（括号不配对、未知字段）时
    未知字段留空、括号错用默认模板。"""
    surnames, _ = normalize_creators(fields.get("creators"))
    values = _Blank(authors=_authors(surnames), year=_year(fields),
                    title=" ".join(str(fields.get("title") or "").split()) or "Untitled")
    try:
        stem = (template or DEFAULT_TEMPLATE).format_map(values)
    except (ValueError, IndexError, AttributeError):
        stem = DEFAULT_TEMPLATE.format_map(values)
    return windows_safe(stem) + ".pdf"


def clean_filename(name: str) -> str:
    """用户在入库框里改过的文件名：同样处理字符，补上 .pdf。"""
    stem = name.strip()
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    return windows_safe(stem) + ".pdf"


# ── 链接 ────────────────────────────────────────────────────


def links_in(text: str) -> list[str]:
    """文本里出现的 http(s) 链接，按出现顺序；去掉句末标点。"""
    return [m.group(0).rstrip(_URL_TAIL) for m in _URL_RE.finditer(text or "")]


def _dedupe(urls: Sequence[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    for url in urls:
        if url.startswith(("https://", "http://")) and url not in out:
            out.append(url)
        if limit is not None and len(out) >= limit:
            break
    return out


def _mentions(citation: Citation, text: str) -> bool:
    """这块原文是不是在说这篇：含它的标识，或含它的标题（格式规整后逐字比较）。"""
    norm = normalize_text(text)
    for key in ("identifier", "title"):
        value = normalize_text(str(citation.fields.get(key) or ""))
        if value and value in norm:
            return True
    return False


def pdf_candidates(citation: Citation, calls: Sequence[ToolCall],
                   limit: int = MAX_PDF_TRIES) -> list[str]:
    """入库时依次试的链接（去重，最多 limit 个），顺序固定：
    ① 段落所在那次调用的参数里的链接（读过全文时就是那个 PDF）；
    ② 引用登记的 url 字段；
    ③ 本次运行里含这篇标识或标题的原文块中出现的链接（按调用顺序、块内出现顺序）。"""
    urls: list[str] = []
    source = next((c for c in calls if c.call_id == citation.call_id), None)
    if source is not None:
        urls.extend(str(v) for v in source.arguments.values() if isinstance(v, str))
    urls.append(str(citation.fields.get("url") or ""))
    for call in sorted(calls, key=lambda c: c.ord):
        if call.tool == CITE_TOOL or call.is_error:
            continue
        for block in call.blocks:
            if _mentions(citation, block.text):
                urls.extend(links_in(block.text))
    return _dedupe([u.strip() for u in urls], limit)


def browser_links(fields: Mapping[str, Any], tried: Sequence[Mapping[str, str]]) -> list[str]:
    """找不到 PDF 时给用户在浏览器里打开、自己下载的链接：DOI 链接、引用的 url、试过的链接。"""
    doi = doi_key(str(fields.get("identifier") or ""))
    urls = [f"https://doi.org/{doi}" if doi else "", str(fields.get("url") or "")]
    urls.extend(str(t.get("url") or "") for t in tried)
    return _dedupe(urls)


__all__ = [
    "DEFAULT_TEMPLATE",
    "IMPORT_DISMISSED",
    "IMPORT_IMPORTED",
    "IMPORT_PENDING_PDF",
    "IMPORT_STATUSES",
    "MAX_PDF_TRIES",
    "PUSH_CLAIMED",
    "PUSH_FAILED",
    "PUSH_PUSHED",
    "ImportRecord",
    "PushRecord",
    "browser_links",
    "clean_filename",
    "doi_key",
    "links_in",
    "paper_filename",
    "pdf_candidates",
    "windows_safe",
]
