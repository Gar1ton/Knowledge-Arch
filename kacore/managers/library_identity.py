"""库内标识（managers 层·P34）：「这篇外部文献是不是库里已有的那篇」的唯一判断处。

为何存在：3.0.0-preview.1 里这件事分散在几处、各自只认 DOI，扩到只有 ISBN 的书时没人跟着改，
于是书会重复出现、入库后还会再出现。现在两个用处——回答时跳过已在库的外部引用
（`api_answer.AskApiMixin._collect_external`）、入库框的「库里已有」（`api_library_io`）——都只调这里。

比对键见 `paper_import.identifier_keys`：规整后的 DOI，或 `isbn:` + ISBN-13。三处来源：
- Zotero 文档：镜像条目的 DOI，与原始字段里的 ISBN（Web API 是 `ISBN`，本地 zotero.sqlite 是
  `fields.ISBN`；归一化字段里没有 ISBN）；
- 本地文档：书目字段（local_meta）里的 DOI；
- 入库来的文档：入库记录 `paper_imports` 里的标识（入库时只往 local_meta 写 DOI，书的 ISBN 只在
  记录里；文档删掉后不算）。

契约：每次现算、不缓存（用户 11-24 选）——入库、拉取、删除之后立刻对，不会过期。
不按书名猜：版次不同 ISBN 不同、纯本地上传又没有 DOI 的书仍认不出（release notes 已知问题）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from kacore.domain.paper_import import IMPORT_IMPORTED, identifier_keys

if TYPE_CHECKING:
    from kacore.repository.source_store.base import SourceDocumentStore


class LibraryIdentity:
    def __init__(self, store: SourceDocumentStore) -> None:
        self._store = store

    async def table(self) -> dict[str, str]:
        """比对表：标识键 → doc_id（同一键先到先得）。"""
        out: dict[str, str] = {}
        docs = await self._store.list_documents()
        doc_ids = {d.doc_id for d in docs}
        by_item = {(d.library_id, d.zotero_item_key): d.doc_id for d in docs if d.zotero_item_key}

        def add(text: str, doc_id: str) -> None:
            for key in identifier_keys(text):
                out.setdefault(key, doc_id)

        for item in await self._store.list_zotero_items():
            doc_id = by_item.get((item.library_id, item.item_key))
            if not doc_id:
                continue
            raw = item.raw_zotero_json or {}
            add(item.doi or "", doc_id)
            add(str(raw.get("ISBN") or (raw.get("fields") or {}).get("ISBN") or ""), doc_id)
        for doc in docs:
            add(str((doc.local_meta or {}).get("doi") or ""), doc.doc_id)
        for record in await self._store.list_imports(status=IMPORT_IMPORTED):
            if record.doc_id in doc_ids:
                add(str(record.fields.get("identifier") or ""), record.doc_id)
        return out

    async def match(self, identifier: str) -> str | None:
        """这个标识对应库里的哪篇文档；对不上（或不是 DOI / ISBN）返回 None。"""
        keys = identifier_keys(identifier)
        if not keys:
            return None
        table = await self.table()
        return next((table[k] for k in keys if k in table), None)

    @staticmethod
    def contains(table: dict[str, str], identifier: str) -> bool:
        """在已算好的比对表里查（一次回答要查很多条引用时只算一次表）。"""
        return any(key in table for key in identifier_keys(identifier))


__all__ = ["LibraryIdentity"]
