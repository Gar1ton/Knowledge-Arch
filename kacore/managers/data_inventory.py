"""把本机的知识库整理成一份可搬走的「库存」目录（managers 层）。

R2 备份（`r2_backup_manager.py`）与 3.0.0 的 `.kaarch` 导出共用：两者的 payload 布局完全相同，
所以 `.kaarch` 的「完全覆盖」可以直接走 R2 的恢复流程（`r2_restore.apply_pending_restore`）。

布局（相对 root）：
- database/knowledge_repository.db    SQLite 在线快照
- library/<doc_id>/…                   制品包；原件不在制品包里的（Zotero linked / ZotMoov）
                                       拷成 original.pdf
- indexes/vector_store.db、embedding_cache.db、index_compatibility.json（有才拷）
- lightrag/…                           图谱工作目录
- config/portable_settings.json        只含可携设置（不含机密、本机路径与 R2 自身凭据）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.managers.r2_backup_manifest import create_sqlite_snapshot_file

if TYPE_CHECKING:
    from kacore.config import Config
    from kacore.repository.source_store.base import SourceDocumentStore
    from kacore.settings.store import SettingsStore


async def build_inventory(
    root: Path,
    *,
    db_path: Path,
    data_dir: Path,
    source_store: SourceDocumentStore,
    config: Config,
    settings: SettingsStore | None,
) -> None:
    """在 root 下写出完整库存（root 不存在时新建）。调用方负责先让索引停下写入。"""
    root.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        create_sqlite_snapshot_file, db_path, root / "database" / "knowledge_repository.db"
    )

    library_target = root / "library"
    library_source = data_dir / "library"
    if library_source.exists():
        await asyncio.to_thread(shutil.copytree, library_source, library_target)
    else:
        library_target.mkdir(parents=True, exist_ok=True)
    for doc in await source_store.list_documents():
        original = Path(doc.file_path)
        portable = library_target / doc.doc_id / "original.pdf"
        if not portable.exists() and original.is_file():
            portable.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, original, portable)

    vector_cfg = config.get_vector_db_config()
    await asyncio.to_thread(
        copy_optional, data_dir / vector_cfg.db_filename, root / "indexes" / "vector_store.db"
    )
    await asyncio.to_thread(
        copy_optional, data_dir / "embedding_cache.db", root / "indexes" / "embedding_cache.db"
    )
    await asyncio.to_thread(
        copy_optional,
        data_dir / "index_compatibility.json",
        root / "indexes" / "index_compatibility.json",
    )

    graph_root = Path(config.get_graph_config().working_dir)
    if not graph_root.is_absolute():
        graph_root = data_dir / graph_root
    if graph_root.exists():
        await asyncio.to_thread(shutil.copytree, graph_root, root / "lightrag")

    (root / "config").mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        root / "config" / "portable_settings.json", portable_settings(config, settings)
    )


def portable_settings(config: Config, settings: SettingsStore | None) -> dict[str, Any]:
    """生产由 SettingsStore 给出；没有 SettingsStore 时从 Config 原始值翻译（测试 / 极简装配）。"""
    if settings is not None:
        return settings.portable_document()
    from kacore.settings.legacy_keys import apply_translation, translate
    from kacore.settings.store import SettingsStore as _SettingsStore

    store = _SettingsStore.in_memory()
    apply_translation(store, translate(config.raw))
    return store.portable_document()


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    os.replace(tmp, path)


def copy_optional(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


__all__ = ["build_inventory", "copy_optional", "portable_settings", "write_json_atomic"]
