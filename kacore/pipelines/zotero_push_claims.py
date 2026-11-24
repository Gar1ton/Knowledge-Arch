"""Zotero 拉取时认领推送过的文档（pipelines 层·3.0.0）。

为何存在：KA 把本地文档推送到 Zotero 后（`managers/zotero_pusher.py`），下次拉取会看到那条新附件。
按普通附件处理会用新编号再建一篇、重新切块和嵌入，原文档还会留着，库里就重复了。
所以拉取先查推送账本：attachment_key 对上的，把原文档改成这条附件的镜像（只改来源字段），
文档编号、切块、向量都不变。
严格镜像的删除判断也用同一张表，否则认领后的文档（编号不是按附件 key 算的）会被当成已删除。

没有推送过时 `push_claims` 为空，拉取行为与 2.2.0 相同。单列成文件是因为 zotero_sync_pipeline.py
已超过文件大小红线。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from kacore.domain.models import DocumentOrigin
from kacore.domain.paper_import import PUSH_CLAIMED

if TYPE_CHECKING:
    from kacore.repository.source_store.base import SourceDocumentStore

logger = logging.getLogger("ZoteroSync")


async def push_claims(store: SourceDocumentStore) -> dict[str, str]:
    """推送账本里的 attachment_key → 原文档编号；本地已删掉的文档不认领（按普通附件新建）。"""
    claims = await store.push_claims()
    return {key: doc_id for key, doc_id in claims.items()
            if await store.get_document(doc_id) is not None}


async def claim_document(store: SourceDocumentStore, doc_id: str, library_id: str,
                         item_key: str, attachment_key: str, version: int) -> None:
    """第一次见到推送过的附件：本地文档改成这条 Zotero 附件的镜像，账本记「已认领」。
    文档已是 Zotero 镜像（之前认领过）时什么也不做。"""
    doc = await store.get_document(doc_id)
    if doc is None or doc.origin is DocumentOrigin.ZOTERO:
        return
    now = datetime.now(timezone.utc)
    doc.library_id, doc.zotero_item_key, doc.attachment_key = library_id, item_key, attachment_key
    doc.origin, doc.read_only, doc.zotero_version = DocumentOrigin.ZOTERO, True, version
    doc.last_synced_at, doc.updated_at = now, now
    await store.update_document(doc)
    record = await store.get_push(doc_id)
    if record is not None:
        record.status = PUSH_CLAIMED
        record.claimed_at = now.isoformat(timespec="seconds")
        await store.save_push(record)
    logger.info("Claimed pushed document %s as Zotero attachment %s", doc_id, attachment_key)


__all__ = ["claim_document", "push_claims"]
