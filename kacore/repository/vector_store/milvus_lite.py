"""repository 层 Milvus Lite 向量投影，拥有本库连接与内嵌服务的生命周期。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.repository.vector_store.base import VectorStore
from kacore.repository.vector_store.milvus_service import MilvusLiteService

if TYPE_CHECKING:
    from collections.abc import Callable

    from kacore.domain.models import DocumentChunk

logger = logging.getLogger("MilvusLiteVectorStore")

_FILTER_KEY_RE = re.compile(r"[A-Za-z0-9_]+")


async def _wait_for_completion(task: asyncio.Task[Any]) -> Any:
    """取消调用者时仍等待线程/清理任务完成，再传播取消，防止数据库操作逃逸。"""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def _quote_filter_value(value: object) -> str:
    """Milvus filter 表达式的字符串值：转义单引号，防表达式注入/语法破坏。"""
    return str(value).replace("'", "\\'")


class MilvusSchemaMismatchError(RuntimeError):
    """Existing Milvus collection cannot be used with the active embedding dimension."""


INDEX_HNSW = "HNSW"
INDEX_FLAT = "FLAT"
_VECTOR_FIELD = "vector"


@dataclass(frozen=True)
class MilvusIndexSpec:
    """向量字段的索引参数（由组合根从 VectorDbConfig 构造）。

    为何默认 HNSW：Milvus Lite 3.1.1 的 FLAT 每检索一个分段都复制一份有效向量再算距离，
    大库单次检索就是数百 MB 的瞬时分配，并发检索叠加即可撑爆内存；HNSW 经 faiss 的
    IDSelector 在原索引上跳过无效行，不复制。`ef_search` 只对 HNSW 生效。
    """

    index_type: str = INDEX_HNSW
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    hnsw_ef_search: int = 64

    def build_params(self) -> dict[str, int]:
        if self.index_type == INDEX_HNSW:
            return {"M": self.hnsw_m, "efConstruction": self.hnsw_ef_construction}
        return {}

    def search_params(self, active_index_type: str | None) -> dict[str, Any] | None:
        """按**当前生效**的索引给检索参数：迁移完成前集合仍是旧索引，不能传 HNSW 专属参数。"""
        if active_index_type == INDEX_HNSW:
            return {"params": {"ef": self.hnsw_ef_search}}
        return None


class MilvusLiteVectorStore(VectorStore):
    """基于 Milvus Lite 的向量检索适配器。

    契约：仅作为 SQLite 文档分块的可重建投影索引。所有写入/删除都是幂等的。

    索引：新集合按 `index` 建；已有集合的索引类型与 `index` 不符时，开库只标记
    `index_migration_pending` 并沿用旧索引检索（开库在启动链上，大库换索引需数十秒），
    由调用方在后台调用 `migrate_index()` 完成更换。集合没有任何索引（换索引中途进程被杀）
    时开库即按 `index` 补建，否则无法 load。
    """

    def __init__(
        self,
        db_path: str = "./data/milvus_lite.db",
        dim: int = 384,
        *,
        index: MilvusIndexSpec | None = None,
        search_concurrency: int = 4,
    ) -> None:
        self._db_path = db_path
        self._dim = dim
        self._index = index or MilvusIndexSpec()
        self._active_index_type: str | None = None
        self._index_migration_pending = False
        # 限制同时在跑的检索线程数：Lite 检索在线程里跑，并发越多瞬时内存越高。
        self._search_slots = asyncio.Semaphore(max(1, int(search_concurrency)))
        self._client: Any = None
        self._service = MilvusLiteService(db_path)
        self._doc_to_col: dict[str, str] = {}
        self._collection_name = "kb_chunks"
        self._initialized = False
        self._created_collection = False
        self._closed = False
        self._pending_calls: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None
        # 懒初始化会在首次检索/写入时触发（开库 + load 秒级），加锁防并发重复初始化。
        self._init_lock = asyncio.Lock()

    @property
    def created_collection(self) -> bool:
        return self._created_collection

    @property
    def index_migration_pending(self) -> bool:
        """已有集合的索引与配置不符、尚未调用 `migrate_index()` 时为 True。"""
        return self._index_migration_pending

    def set_doc_collection_mapping(self, doc_id: str, collection: str) -> None:
        """注册或更新文档与集合的关系。"""
        self._doc_to_col[doc_id] = collection

    def validate_schema(self) -> None:
        """Eagerly validate dependencies and the existing collection schema."""
        self._init_client()

    def _relocate_legacy_store(self) -> None:
        """把 Milvus Lite 2.x 的单文件数据库挪开，让 3.x 以目录形式重建。

        为何需要：Milvus Lite 3.0 起用目录存数据，直接对 2.x 留下的**文件**开库会在
        `os.makedirs(data_dir)` 上抛 `FileExistsError`，对外表现为
        `ConnectionConfigException: Open local milvus failed`，整个向量库就此不可用。

        安全性：本类的契约是「SQLite 文档分块的可重建投影索引」，挪走旧文件不丢原始数据；
        新库为空会让组合根按既有逻辑标记全量 needs_reindex，用户重建索引即可恢复检索。
        """
        path = Path(self._db_path)
        if not path.is_file():  # 目录（3.x）或尚不存在：无需处理
            return

        backup = path.with_name(f"{path.name}.legacy-{int(time.time())}")
        path.rename(backup)
        logger.warning(
            "Found a Milvus Lite 2.x single-file database %s; 3.x stores data in a folder, so it "
            "was renamed to %s and an empty index was created. The original chunks are kept in "
            "SQLite; search recovers after the index is rebuilt.",
            path.name,
            backup.name,
        )

    def _init_client(self) -> None:
        if self._closed:
            raise RuntimeError("Milvus vector store is closed")
        if self._initialized:
            return

        from pymilvus import DataType

        # 确保父目录存在
        db_dir = os.path.dirname(os.path.abspath(self._db_path))
        os.makedirs(db_dir, exist_ok=True)

        self._relocate_legacy_store()
        self._client = self._service.open_client()

        # 如果集合不存在，则创建符合 VARCHAR 主键的 Collection Schema
        if not self._client.has_collection(self._collection_name):
            self._created_collection = True
            schema = self._client.create_schema(
                auto_id=False,
                enable_dynamic_field=True,
            )
            schema.add_field(
                field_name="id", datatype=DataType.VARCHAR, max_length=64, is_primary=True
            )
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self._dim)

            self._client.create_collection(
                collection_name=self._collection_name,
                schema=schema,
                index_params=self._index_params(),
                consistency_level="Strong",
            )
            self._active_index_type = self._index.index_type
            self._index_migration_pending = False
            logger.info(
                f"Created Milvus Lite collection '{self._collection_name}' "
                f"with dimension {self._dim} and {self._index.index_type} index."
            )
        else:
            existing_dim = self._existing_dimension()
            if existing_dim is not None and existing_dim != self._dim:
                raise MilvusSchemaMismatchError(
                    f"Milvus collection dimension is {existing_dim}, but the configured "
                    f"embedding dimension is {self._dim}. Rebuild the Milvus index."
                )
            self._check_existing_index()

        # 打开磁盘上已存在的集合时其处于 released 状态，必须显式 load 才能 search/get/query；
        # 新建集合 create_collection 已自动 load，这里再 load 是幂等的。统一保证 loaded，
        # 避免「跳过 rebuild 直接复用既有索引」时首次检索报 code=101 collection released。
        self._client.load_collection(self._collection_name)
        self._initialized = True

    # ── Index ───────────────────────────────────────────────────

    def _index_params(self) -> Any:
        params = self._client.prepare_index_params()
        params.add_index(
            field_name=_VECTOR_FIELD,
            metric_type="COSINE",
            index_type=self._index.index_type,
            params=self._index.build_params(),
        )
        return params

    def _current_index_type(self) -> str | None:
        """向量字段当前的索引类型；没有索引返回 None。"""
        if _VECTOR_FIELD not in (self._client.list_indexes(self._collection_name) or []):
            return None
        info = self._client.describe_index(self._collection_name, _VECTOR_FIELD) or {}
        value = info.get("index_type")
        return str(value).upper() if value else None

    def _check_existing_index(self) -> None:
        """开库时核对已有集合的索引：缺失即补建；与配置不符只标记待迁移（见类 docstring）。"""
        current = self._current_index_type()
        if current is None:
            logger.warning(
                "Milvus collection '%s' has no vector index (an index change was interrupted); "
                "creating a %s index now, loading may take a while.",
                self._collection_name, self._index.index_type,
            )
            self._client.create_index(self._collection_name, self._index_params())
            current = self._index.index_type
        self._active_index_type = current
        self._index_migration_pending = current != self._index.index_type
        if self._index_migration_pending:
            logger.info(
                "Milvus collection '%s' uses a %s index; switching to %s in the background.",
                self._collection_name, current, self._index.index_type,
            )

    def _migrate_index_sync(self) -> None:
        """释放 → 删旧索引 → 建新索引 → load（建索引发生在 load 内）。单个同步函数，一次跑完。"""
        client = self._client
        client.release_collection(self._collection_name)
        try:
            if self._current_index_type() is not None:
                client.drop_index(self._collection_name, _VECTOR_FIELD)
            client.create_index(self._collection_name, self._index_params())
        finally:
            # 无论成败都要回到 loaded：建新索引失败时退回原索引（若已删则由下次开库补建）。
            if self._current_index_type() is None:
                client.create_index(self._collection_name, self._index_params())
            client.load_collection(self._collection_name)
            self._active_index_type = self._current_index_type()

    async def migrate_index(self) -> bool:
        """把已有集合的索引换成配置值；无需更换时立即返回 False。

        持 `_init_lock` 并先等在途操作结束；更换期间新的检索 / 写入在 `_ensure_client`
        处排队，换完继续（不报错）。失败时集合仍回到 loaded，异常向上抛给调用方记录。
        """
        if not self._index_migration_pending or self._closed:
            return False
        async with self._init_lock:
            if not self._index_migration_pending or self._closed:
                return False
            self._initialized = False
            try:
                if self._pending_calls:
                    await asyncio.gather(*self._pending_calls, return_exceptions=True)
                started = time.monotonic()
                await self._run_sync(self._migrate_index_sync)
                self._index_migration_pending = self._active_index_type != self._index.index_type
                logger.info(
                    "Milvus index switched to %s in %.1fs.",
                    self._active_index_type, time.monotonic() - started,
                )
            finally:
                self._initialized = True
        return not self._index_migration_pending

    async def _ensure_client(self) -> None:
        """异步侧的懒初始化入口：to_thread + 锁，避免开库/load 阻塞事件循环或并发重入。"""
        if self._closed:
            raise RuntimeError("Milvus vector store is closed")
        if self._initialized:
            return
        async with self._init_lock:
            if not self._initialized:
                await self._run_sync(self._init_client)

    async def _run_sync(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """记录阻塞操作，关闭时拒绝新操作，并等已提交的线程结束。"""
        if self._closed:
            raise RuntimeError("Milvus vector store is closed")
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self._pending_calls.add(task)
        task.add_done_callback(self._pending_calls.discard)
        return await _wait_for_completion(task)

    def _existing_dimension(self) -> int | None:
        description = self._client.describe_collection(self._collection_name)
        for field in description.get("fields", []):
            if (field.get("name") or field.get("field_name")) != "vector":
                continue
            params = field.get("params") or {}
            value = params.get("dim") or field.get("dim")
            return int(value) if value is not None else None
        return None

    def _validate_vector(self, vector: list[float]) -> None:
        if len(vector) != self._dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._dim}, got {len(vector)}."
            )

    async def upsert_chunks(
        self, chunks: list[DocumentChunk], embeddings: list[list[float]]
    ) -> None:
        if not chunks or not embeddings:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")

        for embedding in embeddings:
            self._validate_vector(embedding)
        await self._ensure_client()

        data = []
        for chunk, emb in zip(chunks, embeddings):
            col = self._doc_to_col.get(chunk.doc_id) or "default"
            data.append({
                "id": chunk.chunk_id,
                "vector": emb,
                "doc_id": chunk.doc_id,
                "collection_tag": col,
                "text": chunk.text,
            })

        logger.info("Milvus upsert: %d chunks", len(chunks))
        try:
            await self._run_sync(
                self._client.upsert, collection_name=self._collection_name, data=data
            )
        except Exception as e:
            logger.warning(f"Milvus client.upsert failed ({e}), falling back to delete & insert...")
            chunk_ids = [c.chunk_id for c in chunks]
            try:
                await self._run_sync(
                    self._client.delete, collection_name=self._collection_name, ids=chunk_ids
                )
            except Exception as del_exc:
                logger.debug("Milvus pre-insert delete failed (best-effort): %s", del_exc)
            await self._run_sync(
                self._client.insert, collection_name=self._collection_name, data=data
            )

    async def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        await self._ensure_client()
        try:
            await self._run_sync(
                self._client.delete, collection_name=self._collection_name, ids=chunk_ids
            )
        except Exception as e:
            logger.error(f"Failed to delete chunks: {e}")
            raise RuntimeError(f"Failed to delete chunks: {e}") from e

    async def retain_document_chunks(self, doc_id: str, chunk_ids: frozenset[str]) -> None:
        await self._ensure_client()
        expression = f"doc_id == '{_quote_filter_value(doc_id)}'"
        if chunk_ids:
            ids = ", ".join(f"'{_quote_filter_value(cid)}'" for cid in sorted(chunk_ids))
            expression += f" and id not in [{ids}]"
        await self._run_sync(
            self._client.delete, collection_name=self._collection_name, filter=expression,
        )

    async def delete_collection(self, collection: str) -> None:
        await self._ensure_client()
        try:
            # 根据 collection_tag 属性删除匹配的向量数据
            await self._run_sync(
                self._client.delete,
                collection_name=self._collection_name,
                filter=f"collection_tag == '{_quote_filter_value(collection)}'",
            )
            # 清理映射缓存
            self._doc_to_col = {
                d: c for d, c in self._doc_to_col.items() if c != collection
            }
        except Exception as e:
            logger.error(f"Failed to delete collection {collection}: {e}")
            raise RuntimeError(f"Failed to delete collection {collection}: {e}") from e

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int,
        filter_metadata: dict | None = None,
        *,
        exclude_chunk_ids: frozenset[str] | None = None,
        allowed_doc_ids: frozenset[str] | None = None,
    ) -> list[tuple[str, float]]:
        if top_k <= 0 or allowed_doc_ids == frozenset():
            return []

        self._validate_vector(query_vector)
        # 先占检索名额再确认开库：`_ensure_client` 在已开库时不让出事件循环，紧接着的 `_run_sync`
        # 同步登记在途任务，后台换索引因此总能看到并等待它（反过来先开库再排队会留下竞态窗口）。
        async with self._search_slots:
            return await self._search_ready(
                collection, query_vector, top_k, filter_metadata,
                exclude_chunk_ids=exclude_chunk_ids, allowed_doc_ids=allowed_doc_ids,
            )

    async def _search_ready(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int,
        filter_metadata: dict | None,
        *,
        exclude_chunk_ids: frozenset[str] | None,
        allowed_doc_ids: frozenset[str] | None,
    ) -> list[tuple[str, float]]:
        await self._ensure_client()

        clauses: list[str] = []
        if collection:  # 空串 = 不按集合过滤（见 base 契约）
            clauses.append(f"collection_tag == '{_quote_filter_value(collection)}'")
        if filter_metadata:
            for k, v in filter_metadata.items():
                if not _FILTER_KEY_RE.fullmatch(k):
                    raise ValueError(f"Invalid metadata filter key: {k!r}")
                clauses.append(f"{k} == '{_quote_filter_value(v)}'")
        if allowed_doc_ids is not None:
            docs = ", ".join(f"'{_quote_filter_value(d)}'" for d in sorted(allowed_doc_ids))
            clauses.append(f"doc_id in [{docs}]")
        if exclude_chunk_ids:
            # 用 Milvus 原生 `not in` 表达式在候选截断前下推排除，不做事后过滤
            # （W0 隔离探针已验证 pymilvus 2.6.x + milvus-lite 3.x 对动态字段同样生效）。
            excluded = ", ".join(f"'{_quote_filter_value(cid)}'" for cid in exclude_chunk_ids)
            clauses.append(f"id not in [{excluded}]")
        filter_expr = " and ".join(clauses)

        search_kwargs: dict[str, Any] = {}
        search_params = self._index.search_params(self._active_index_type)
        if search_params is not None:
            search_kwargs["search_params"] = search_params
        try:
            results = await self._run_sync(
                self._client.search,
                collection_name=self._collection_name,
                data=[query_vector],
                limit=top_k,
                filter=filter_expr,
                output_fields=["id"],
                **search_kwargs,
            )

            res_list = []
            if results and len(results) > 0:
                for r in results[0]:
                    res_list.append((r["id"], r["distance"]))
            return res_list
        except Exception as e:
            logger.error(f"Milvus search failed: {e}")
            raise RuntimeError(f"Milvus search failed: {e}") from e

    def _clear_sync(self) -> None:
        if self._client is None:
            self._client = self._service.open_client()
        if self._client.has_collection(self._collection_name):
            self._client.drop_collection(self._collection_name)
        self._client.close()
        self._client = None
        self._initialized = False
        self._doc_to_col.clear()
        self._init_client()

    async def clear(self) -> None:
        try:
            # 与懒初始化共用同一把锁：drop + 重建期间不允许并发触发 _ensure_client。
            async with self._init_lock:
                await self._run_sync(self._clear_sync)
        except Exception as e:
            logger.error(f"Failed to clear vector store: {e}")
            raise RuntimeError(f"Failed to clear vector store: {e}") from e

    async def close(self) -> None:
        """停止新操作，等待在途线程，刷盘并释放本库 Lite 服务；重复调用共用清理任务。"""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await _wait_for_completion(self._close_task)

    async def _close(self) -> None:
        async with self._init_lock:
            if self._pending_calls:
                await asyncio.gather(*self._pending_calls, return_exceptions=True)
            await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        client = self._client
        try:
            if client is not None and self._initialized:
                client.flush(self._collection_name)
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                try:
                    # client.close 只断开 RPC；通过开库时保留的所有者释放服务与 LOCK。
                    self._service.release()
                finally:
                    self._client = None
                    self._initialized = False


__all__ = ["MilvusIndexSpec", "MilvusLiteVectorStore", "MilvusSchemaMismatchError"]
