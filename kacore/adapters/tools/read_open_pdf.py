"""读开放获取 PDF 的工具（adapters 层）：下载到临时缓存、按页切段、按问题挑出最相关的几段。

为何存在：外部引用要能落到页，摘要只说结论。全文只进临时缓存（数据目录 cache/external/，按内容
sha256 命名，同一文件只存一份），不算入库。只接受以 `%PDF-` 开头的响应（不少「开放获取」链接其实是
出版社网页），超过大小上限即放弃。挑段用用户配置的 Rerank 模型；Rerank 关着时这个工具不可用，
交给模型的说明里会讲清楚。每段带「page N」位置，引用的页码由此而来。
"""
from __future__ import annotations

import asyncio
import hashlib
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.adapters.tools.base import USER_AGENT, Tool, ToolEnv, ToolError
from kacore.domain.models import DocumentChunk
from kacore.domain.tool_calls import SETTING_INT, Block, ToolResult, ToolSetting, ToolSpec

if TYPE_CHECKING:
    from kacore.repository.reranker.base import Reranker

_PDF_MAGIC = b"%PDF-"
_READ_BLOCK = 256 * 1024
# 段落长度：页内按空行聚段，再合并到不超过这个长度（与库内分块的量级相当，假设值）。
PASSAGE_CHARS = 1200
_MIN_PASSAGE_CHARS = 200
_MAX_PAGES = 80
# 每次返回几段：默认与上限（假设值；模型可以换个 query 再读）。
DEFAULT_PASSAGES = 3
MAX_PASSAGES = 6
DEFAULT_MAX_PDF_MB = 40


@dataclass(frozen=True)
class PagePassage:
    page: int      # 从 1 起
    text: str


class ReadOpenPdf(Tool):
    spec = ToolSpec(
        name="read_open_pdf",
        description=(
            "Download an open-access PDF (a link from a search result) and return the passages "
            "most relevant to `query`, each marked with its page. Use it to quote a paper's own "
            "text instead of its abstract."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Open-access PDF link."},
                "query": {"type": "string",
                          "description": "What the passages should be about."},
                "passages": {"type": "integer", "minimum": 1, "maximum": MAX_PASSAGES,
                             "description": f"How many passages (default {DEFAULT_PASSAGES})."},
            },
            "required": ["url", "query"],
        },
        settings=(ToolSetting("max_pdf_mb", SETTING_INT, DEFAULT_MAX_PDF_MB),),
    )

    def __init__(self, *, cache_dir: Path, reranker: Reranker | None,
                 max_pdf_mb: int = DEFAULT_MAX_PDF_MB,
                 opener: Callable[..., Any] | None = None, timeout: float = 30.0) -> None:
        self._root = cache_dir
        self._reranker = reranker
        self._max_bytes = max(1, int(max_pdf_mb)) * 1024 * 1024
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout

    @classmethod
    def from_settings(cls, values: Mapping[str, Any], env: ToolEnv) -> ReadOpenPdf:
        return cls(cache_dir=env.cache_dir, reranker=env.reranker,
                   max_pdf_mb=int(values.get("max_pdf_mb") or DEFAULT_MAX_PDF_MB))

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        query = " ".join(str(arguments.get("query") or "").split())
        if not url or not query:
            raise ValueError("url and query are required")
        if self._reranker is None or self._reranker.is_passthrough:
            return ToolResult.error(
                "Reading full text needs the Rerank model, which is off; cite the abstract.")
        try:
            wanted = min(max(int(arguments.get("passages") or DEFAULT_PASSAGES), 1), MAX_PASSAGES)
        except (TypeError, ValueError):
            wanted = DEFAULT_PASSAGES
        path = await asyncio.to_thread(self.fetch, url)
        passages = await asyncio.to_thread(page_passages, path)
        if not passages:
            return ToolResult.error("The PDF has no text layer (a scan); cite the abstract.")
        chunks = [DocumentChunk(chunk_id=f"p{i}", doc_id=path.stem, ordinal=i, text=p.text,
                                content_hash="", metadata={"page_number": p.page})
                  for i, p in enumerate(passages)]
        scored = await self._reranker.rerank(query, chunks, top_n=wanted)
        pages = [int(s.chunk.metadata["page_number"]) for s in scored[:wanted]]
        return ToolResult([Block(s.chunk.text, f"page {page}", page)
                           for s, page in zip(scored, pages)])

    def fetch(self, url: str) -> Path:
        """下载一个 PDF 进缓存并返回路径；不是 PDF、超过上限或网络失败抛 ToolError。"""
        if not url.startswith(("https://", "http://")):
            raise ToolError("not an http(s) link")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        try:
            with self._opener(request, timeout=self._timeout) as resp:
                while block := resp.read(_READ_BLOCK):
                    if not chunks and not block.lstrip()[:5].startswith(_PDF_MAGIC):
                        raise ToolError("the link does not return a PDF (probably a web page)")
                    size += len(block)
                    if size > self._max_bytes:
                        raise ToolError(f"the PDF is larger than {self._max_bytes // 2**20} MB")
                    hasher.update(block)
                    chunks.append(block)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下载失败统一说明给模型
            raise ToolError(f"download failed: {exc}") from exc
        if not chunks:
            raise ToolError("empty response")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{hasher.hexdigest()}.pdf"
        if not path.exists():
            tmp = path.with_suffix(".part")
            tmp.write_bytes(b"".join(chunks))
            tmp.replace(path)
        return path


def page_passages(path: Path, *, max_chars: int = PASSAGE_CHARS,
                  max_pages: int = _MAX_PAGES) -> list[PagePassage]:
    """按页抽文字并切成段落（PyMuPDF 纯文字层；扫描件没有文字层时返回空列表）。"""
    import pymupdf

    out: list[PagePassage] = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            out.extend(PagePassage(index + 1, p)
                       for p in _split(page.get_text("text") or "", max_chars))
    return out


def _split(text: str, max_chars: int) -> list[str]:
    """按空行聚段，再把相邻短段合并到不超过 max_chars；过短的残段丢弃。"""
    merged: list[str] = []
    buf = ""
    for para in (" ".join(p.split()) for p in text.split("\n\n")):
        if not para:
            continue
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


def cache_dir_for(data_dir: Path) -> Path:
    return data_dir / "cache" / "external"


__all__ = ["PASSAGE_CHARS", "PagePassage", "ReadOpenPdf", "cache_dir_for", "page_passages"]
