"""外部文献的标识、去重与核验规则（domain 层·纯函数，零依赖）。

为何存在：3.0.0 起回答可以引用外部文献（对话栏打开「联网补充」时）。外部文献来自多个学术索引
（Crossref / arXiv / OpenAlex / Semantic Scholar），同一篇论文会被不同索引以不同形态返回；要把它们
归并成一条记录、判断能不能引用，并且判断过程可复现、可追溯。

本模块只认识字符串与本文件定义的数据类，不认识任何索引的 HTTP 形态（那是 adapters 的事），
也不调用模型：核验只看索引返回的结构化字段——持久标识、第二个索引能否按编号查到、撤稿标记。
不用标题相似度阈值（阈值要靠样本调参）；只做格式规整（DOI 小写、去 arXiv 版本号、去跟踪参数），
不判断文本含义。

去重键的优先级参照 ai-source-scraper 1.7.0 的做法（按净化后的 URL 去重），但把学术标识排在 URL 前：
DOI > arXiv 号 > PMID > ISBN > OpenAlex 作品号 > 净化后的 URL。ISBN 让只有 ISBN、没有 DOI 的书
（人文领域的专著常见）也能核验与引用。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ── 标识 ────────────────────────────────────────────────────

PID_DOI = "doi"
PID_ARXIV = "arxiv"
PID_PMID = "pmid"
PID_ISBN = "isbn"
PID_OPENALEX = "openalex"
PID_URL = "url"
# 可核验的持久标识：URL 与 OpenAlex 作品号不算（前者会变，后者只是单一索引的内部编号）。
PERSISTENT_KINDS = frozenset({PID_DOI, PID_ARXIV, PID_PMID, PID_ISBN})
PID_ORDER = (PID_DOI, PID_ARXIV, PID_PMID, PID_ISBN, PID_OPENALEX, PID_URL)

_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:",
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD_RE = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")
_ARXIV_PREFIXES = ("arxiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/")
_OPENALEX_RE = re.compile(r"^W\d+$")

# 跟踪参数：utm_* 之外与 ai-source-scraper 1.7.0 的 TRACKING_PARAMS 相同（15 个）。
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "gbraid", "wbraid", "msclkid", "dclid", "yclid", "mc_cid", "mc_eid",
    "igshid", "_hsenc", "_hsmi", "vero_id", "oly_anon_id", "oly_enc_id",
})


def normalize_doi(raw: str) -> str:
    """DOI 统一为小写、不带解析前缀；不是 DOI 形态返回空串。"""
    text = (raw or "").strip()
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().lower()
    return text if _DOI_RE.match(text) else ""


def normalize_arxiv(raw: str) -> str:
    """arXiv 号去掉前缀与版本号（2601.01234v2 → 2601.01234）；不是 arXiv 形态返回空串。"""
    text = (raw or "").strip()
    lowered = text.lower()
    for prefix in _ARXIV_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip()
    match = _ARXIV_NEW_RE.match(text) or _ARXIV_OLD_RE.match(text)
    return match.group(1) if match else ""


def normalize_isbn(raw: str) -> str:
    """ISBN 去掉连字符与空格并校验校验位；ISBN-10 统一转成 ISBN-13。不合格返回空串。"""
    text = "".join(ch for ch in (raw or "").upper() if ch.isdigit() or ch == "X")
    if len(text) == 10 and text[:9].isdigit():
        total = sum((10 - i) * int(ch) for i, ch in enumerate(text[:9]))
        check = 10 if text[9] == "X" else int(text[9]) if text[9].isdigit() else -1
        if (total + check) % 11 != 0:
            return ""
        text = "978" + text[:9]
        return text + str((10 - sum((3 if i % 2 else 1) * int(c)
                                     for i, c in enumerate(text)) % 10) % 10)
    if len(text) == 13 and text.isdigit():
        digits = [int(c) for c in text]
        if sum((3 if i % 2 else 1) * d for i, d in enumerate(digits)) % 10 == 0:
            return text
    return ""


def clean_url(raw: str) -> str:
    """去掉 utm_* 与常见跟踪参数、去掉 #片段。

    与 ai-source-scraper 的 keyOf 一致：不改 www、协议与尾斜杠。
    """
    if not raw:
        return ""
    parts = urlsplit(raw.strip())
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


@dataclass(frozen=True)
class Pid:
    """一个标识：kind ∈ {doi, arxiv, pmid, openalex, url}，value 已规整。"""

    kind: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"

    @property
    def persistent(self) -> bool:
        return self.kind in PERSISTENT_KINDS


# ── 索引命中与归并 ──────────────────────────────────────────


@dataclass
class IndexHit:
    """某个索引对某次检索词返回的一条结果（adapters 翻译后的形态）。

    契约：标识字段已由 adapter 原样填入（可带前缀），由本模块规整；fields 是该索引给出的书目字段
    （title / creators / year / venue / type / abstract / oa_url / ...），用于字段级出处。
    reference_ids 是该条目的参考文献标识（任意可识别形态），用于引用图谱。
    """

    engine: str
    query: str
    rank: int
    doi: str = ""
    arxiv: str = ""
    pmid: str = ""
    openalex: str = ""
    url: str = ""
    isbns: list[str] = field(default_factory=list)
    fields: dict[str, object] = field(default_factory=dict)
    reference_ids: list[str] = field(default_factory=list)
    retracted: bool = False

    def pids(self) -> list[Pid]:
        """按优先级列出该命中携带的所有可识别标识（同一篇论文可能同时有 DOI 与 arXiv 号）。"""
        out: list[Pid] = []
        if doi := normalize_doi(self.doi):
            out.append(Pid(PID_DOI, doi))
        if arxiv := normalize_arxiv(self.arxiv):
            out.append(Pid(PID_ARXIV, arxiv))
        if self.pmid.strip().isdigit():
            out.append(Pid(PID_PMID, self.pmid.strip()))
        for raw in self.isbns:                       # 一本书常有多个 ISBN（版次 / 装帧），都作别名
            if (isbn := normalize_isbn(raw)) and all(p.value != isbn for p in out):
                out.append(Pid(PID_ISBN, isbn))
        openalex = self.openalex.strip().rsplit("/", 1)[-1]
        if _OPENALEX_RE.match(openalex):
            out.append(Pid(PID_OPENALEX, openalex))
        if not out and (url := clean_url(self.url)):
            out.append(Pid(PID_URL, url))
        return out


def canonical_id(hit: IndexHit) -> str:
    """去重键：DOI > arXiv > PMID > ISBN > OpenAlex 作品号 > 净化后的 URL；什么都没有返回空串。"""
    pids = hit.pids()
    return pids[0].key if pids else ""


@dataclass
class SourceRecord:
    """归并后的一篇外部文献。

    pid 是首选标识（canonical_id 的那一个）；aliases 是同一篇论文的其它标识
    （如同时给出的 arXiv 号），用于把不同索引的命中归到一起。found_by 是最先给出这篇的索引；
    lookup_hits 是「拿编号去查、查到了」的索引集合（由 managers 层的核验步骤填写）。
    field_origin 记录每个书目字段取自哪个索引（字段级出处）。
    """

    source_id: str
    pid: Pid
    aliases: set[str] = field(default_factory=set)
    hits: list[IndexHit] = field(default_factory=list)
    fields: dict[str, object] = field(default_factory=dict)
    field_origin: dict[str, str] = field(default_factory=dict)
    reference_ids: set[str] = field(default_factory=set)
    found_by: str = ""
    lookup_hits: set[str] = field(default_factory=set)
    retracted: bool = False
    doi_resolves: bool | None = None

    @property
    def engines(self) -> list[str]:
        """给出过这篇的索引（去重、按首次出现顺序）。"""
        return list(dict.fromkeys(h.engine for h in self.hits))


def merge_hits(hits: Iterable[IndexHit]) -> list[SourceRecord]:
    """把多个索引的命中按标识归并为文献记录，保持首次出现的顺序。

    任一标识相同即视为同一篇（DOI 相同、或 arXiv 号相同……）；不按标题猜同一篇。
    字段取首个给出非空值的索引，并记入 field_origin。无任何标识的命中被丢弃。
    """
    records: list[SourceRecord] = []
    by_key: dict[str, SourceRecord] = {}
    for hit in hits:
        pids = hit.pids()
        if not pids:
            continue
        keys = [p.key for p in pids]
        record = next((by_key[k] for k in keys if k in by_key), None)
        if record is None:
            record = SourceRecord(source_id=keys[0], pid=pids[0], found_by=hit.engine)
            records.append(record)
        for k in keys:
            record.aliases.add(k)
            by_key.setdefault(k, record)
        _upgrade_pid(record, pids)
        record.hits.append(hit)
        record.retracted = record.retracted or hit.retracted
        record.reference_ids.update(r for r in hit.reference_ids if r)
        for name, value in hit.fields.items():
            if _filled(value) and not _filled(record.fields.get(name)):
                record.fields[name] = value
                record.field_origin[name] = hit.engine
    return records


def _upgrade_pid(record: SourceRecord, pids: list[Pid]) -> None:
    """后来的命中带了更可靠的标识（如 OpenAlex 号之后出现 DOI）时，首选标识随之升级。"""
    best = min(pids, key=lambda p: PID_ORDER.index(p.kind))
    if PID_ORDER.index(best.kind) < PID_ORDER.index(record.pid.kind):
        record.pid = best
        record.source_id = best.key


def _filled(value: object) -> bool:
    return value not in (None, "", [], {})


# ── 核验 ────────────────────────────────────────────────────


class Trust(str, Enum):
    """一篇外部文献能不能被回答引用。"""

    VERIFIED = "verified"      # ✓ 已核验：可以引用
    SINGLE = "single"          # · 单源：可以引用，回答里标「未核验」
    CONFLICT = "conflict"      # ⚠ DOI 在 doi.org 查不到：疑似编造，不引用
    RETRACTED = "retracted"    # ✕ 已撤稿：不引用，来源栏照列
    UNRESOLVED = "unresolved"  # ? 没有持久标识：不引用


CITABLE = frozenset({Trust.VERIFIED, Trust.SINGLE})


def trust_level(record: SourceRecord) -> Trust:
    """按结构化字段给出可信状态（顺序即优先级）。

    ① 撤稿标记 → RETRACTED；② 没有 DOI / arXiv 号 / PMID / ISBN → UNRESOLVED；
    ③ DOI 已确认在 doi.org 查不到（doi_resolves is False）→ CONFLICT；
    ④ 除最先给出它的索引外，还有别的索引按编号查到它 → VERIFIED；⑤ 否则 SINGLE。
    doi_resolves 为 None 表示还没查，不据此降级。
    """
    if record.retracted:
        return Trust.RETRACTED
    if not record.pid.persistent:
        return Trust.UNRESOLVED
    if record.pid.kind == PID_DOI and record.doi_resolves is False:
        return Trust.CONFLICT
    confirmed = (record.lookup_hits | set(record.engines)) - {record.found_by}
    return Trust.VERIFIED if confirmed else Trust.SINGLE


def rank_drift(ranks_by_capture: Mapping[str, int], order: list[str]) -> tuple[int, int] | None:
    """同一对话里相邻两次出现的排名变化（ai-source-scraper 1.7 的 rank drift）；没有变化返回 None。

    order 是该对话里 capture 的时间顺序；只比较最后两次出现。
    """
    seen = [ranks_by_capture[c] for c in order if c in ranks_by_capture]
    if len(seen) < 2 or seen[-1] == seen[-2]:
        return None
    return seen[-2], seen[-1]


__all__ = [
    "CITABLE",
    "PERSISTENT_KINDS",
    "PID_ARXIV",
    "PID_DOI",
    "PID_ISBN",
    "PID_OPENALEX",
    "PID_ORDER",
    "PID_PMID",
    "PID_URL",
    "TRACKING_PARAMS",
    "IndexHit",
    "Pid",
    "SourceRecord",
    "Trust",
    "canonical_id",
    "clean_url",
    "merge_hits",
    "normalize_arxiv",
    "normalize_doi",
    "normalize_isbn",
    "rank_drift",
    "trust_level",
]
