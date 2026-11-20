"""外部来源账本与 Zotero 推送账本的记录类型（domain 层·纯数据，零依赖）。

与迁移 029 / 030 一一对应；仓储（repository/source_store/external_*.py）只负责持久化这些记录。
记账方式说明见 `migrations/029_external_sources.sql`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

ACTOR_UI = "ui"
ACTOR_AGENT = "agent"

PUSH_QUEUED = "queued"
PUSH_PLACING = "placing"
PUSH_CREATING = "creating"
PUSH_PUSHED = "pushed"
PUSH_ADOPTED = "adopted"
PUSH_FAILED = "failed"


@dataclass
class Capture:
    """一次联网补充（ai-source-scraper 的 capture）。msg_idx 是回答在对话中的序号。"""

    capture_id: str
    conv_id: str
    msg_idx: int
    question: str
    queries: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    actor: str = ACTOR_UI
    captured_at: str = ""


@dataclass
class StoredSource:
    """账本里的一篇外部文献。first_seen 只在首次写入时设定，之后更新不改它。"""

    source_id: str
    trust: str
    aliases: list[str] = field(default_factory=list)
    meta: dict[str, object] = field(default_factory=dict)
    reference_ids: list[str] = field(default_factory=list)
    placement: dict[str, object] = field(default_factory=dict)
    first_seen: str = ""
    checked_at: str = ""


@dataclass
class CaptureHit:
    """某次联网补充里某个索引给出的某一条；is_new = 该对话里首次出现。"""

    capture_id: str
    source_id: str
    engine: str
    query: str
    rank: int
    is_new: bool = True
    request_url: str = ""


@dataclass
class Citation:
    """回答里真正引用的一段：page 为 None 表示只用了摘要。"""

    capture_id: str
    source_id: str
    ord: int
    passage: str
    page: int | None = None
    file_sha256: str = ""


@dataclass
class FieldOrigin:
    """字段级出处：某篇文献的某个书目字段取自哪个索引、何时取得。"""

    source_id: str
    field: str
    engine: str
    value: str
    fetched_at: str


@dataclass
class PushEntry:
    """一篇本地文档推送回 Zotero 的进度（迁移 030）。"""

    doc_id: str
    status: str = PUSH_QUEUED
    library_id: str = ""
    item_key: str = ""
    target_coll: str = ""
    file_path: str = ""
    source_path: str = ""
    message: str = ""
    updated_at: str = ""
    pushed_at: str | None = None
    adopted_at: str | None = None


__all__ = [
    "ACTOR_AGENT",
    "ACTOR_UI",
    "PUSH_ADOPTED",
    "PUSH_CREATING",
    "PUSH_FAILED",
    "PUSH_PLACING",
    "PUSH_PUSHED",
    "PUSH_QUEUED",
    "Capture",
    "CaptureHit",
    "Citation",
    "FieldOrigin",
    "PushEntry",
    "StoredSource",
]
