"""外部文献的开放获取全文：下载到临时缓存、按页切出段落（managers 层）。

为何存在：联网补充的 depth=fulltext 时，外部引用要能定位到页，摘要只说结论不够支撑具体论断。
全文只进临时缓存（数据目录 cache/external/），不算入库；用户在来源栏点「入库」才正式进库
（入库时直接复用这里的文件，不再下载）。

缓存按 PDF 内容的 sha256 命名，同一文件只存一份；文件指纹随引用记进出处账本，
缓存清掉后仍能核对当时读到的是哪一份。只接受以 `%PDF-` 开头的响应（不少「开放获取」链接
其实是出版社的网页），超过大小上限即放弃。
"""
from __future__ import annotations

import hashlib
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kacore.adapters.scholarly.base import USER_AGENT

logger = logging.getLogger("ExternalFulltext")

_PDF_MAGIC = b"%PDF-"
_READ_BLOCK = 256 * 1024
# 段落长度：页内按空行聚段，再合并到不超过这个长度（与库内分块的量级相当，假设值）。
PASSAGE_CHARS = 1200
_MIN_PASSAGE_CHARS = 200


@dataclass(frozen=True)
class CachedPdf:
    path: Path
    sha256: str
    size: int


@dataclass(frozen=True)
class PagePassage:
    page: int      # 从 1 起
    text: str


class FulltextCache:
    """opener 可注入（测试用）；fetch 是同步阻塞调用，调用方放进线程执行。"""

    def __init__(self, root: Path, *, max_bytes: int, opener: Callable[..., Any] | None = None,
                 timeout: float = 30.0) -> None:
        self._root = root
        self._max = max_bytes
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout

    def fetch(self, url: str) -> CachedPdf | None:
        """下载一个 PDF；不是 PDF、超过上限或网络失败都返回 None（不抛异常，外部证据退回摘要）。"""
        if not url.startswith(("https://", "http://")):
            return None
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        try:
            with self._opener(request, timeout=self._timeout) as resp:
                while True:
                    block = resp.read(_READ_BLOCK)
                    if not block:
                        break
                    if not chunks and not block.lstrip()[:5].startswith(_PDF_MAGIC):
                        logger.info("Open-access link is not a PDF, using the abstract: %s", url)
                        return None
                    size += len(block)
                    if size > self._max:
                        logger.info("Open-access PDF exceeds %d bytes, skipped: %s", self._max, url)
                        return None
                    hasher.update(block)
                    chunks.append(block)
        except Exception as exc:  # noqa: BLE001 - 任何下载失败都退回摘要
            logger.info("Open-access PDF download failed (%s): %s", exc, url)
            return None
        if not chunks:
            return None
        digest = hasher.hexdigest()
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{digest}.pdf"
        if not path.exists():
            tmp = path.with_suffix(".part")
            tmp.write_bytes(b"".join(chunks))
            tmp.replace(path)
        return CachedPdf(path=path, sha256=digest, size=size)

    def find(self, sha256: str) -> Path | None:
        """入库时按指纹取回已缓存的文件；不在了返回 None。"""
        path = self._root / f"{sha256}.pdf"
        return path if path.exists() else None


def page_passages(path: Path, *, max_chars: int = PASSAGE_CHARS,
                  max_pages: int = 80) -> list[PagePassage]:
    """按页抽文字并切成段落（PyMuPDF 纯文字层；扫描件没有文字层时返回空列表）。"""
    import pymupdf

    out: list[PagePassage] = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            text = page.get_text("text") or ""
            out.extend(PagePassage(index + 1, p) for p in _split(text, max_chars))
    return out


def _split(text: str, max_chars: int) -> list[str]:
    """按空行聚段，再把相邻短段合并到不超过 max_chars；过短的残段丢弃。"""
    paras = [" ".join(p.split()) for p in text.split("\n\n")]
    merged: list[str] = []
    buf = ""
    for para in (p for p in paras if p):
        if buf and len(buf) + 1 + len(para) > max_chars:
            merged.append(buf)
            buf = para
        else:
            buf = f"{buf} {para}".strip()
    if buf:
        merged.append(buf)
    out: list[str] = []
    for piece in merged:
        while len(piece) > max_chars:
            out.append(piece[:max_chars])
            piece = piece[max_chars:]
        out.append(piece)
    return [p for p in out if len(p) >= _MIN_PASSAGE_CHARS]


__all__ = ["PASSAGE_CHARS", "CachedPdf", "FulltextCache", "PagePassage", "page_passages"]
