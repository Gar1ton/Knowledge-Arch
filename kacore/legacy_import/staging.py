"""旧版插件数据 → staging 副本（复制、改写路径、迁移、翻译配置）。

全程只读旧目录；所有写入都落在 `<data>/.legacy_import/staging/<id>/`，任何一步失败都删除
整个 staging 目录，数据目录本身不受影响。换入发生在下一次服务初始化（见 apply.py）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from kacore.config import SourceStoreConfig, VectorDbConfig
from kacore.legacy_import.layout import (
    PATH_MARKER,
    LegacyImportError,
    LegacyInstall,
    locate,
)

STATE_DIR = ".legacy_import"
PENDING_FILE = "pending.json"
APPLIED_FILE = "applied.json"
RESULT_FILE = "result.json"
STAGING_DIR = "staging"
PAYLOAD_DIR = "payload"
CONFIG_DIR = "config"
SECRETS_DIR = "secrets"
PLUGIN_CONFIG_COPY = "plugin_config.json"
RUNTIME_CONFIG_COPY = "runtime_config.json"
DB_FILENAME = SourceStoreConfig.db_filename
VECTOR_FILENAME = VectorDbConfig.db_filename
# 不复制的顶层条目：由新版自己管理（设置 / 机密 / 日志 / 组件 / 锁）或另行处理（旧配置、机密）。
_EXCLUDED_NAMES = frozenset({
    "settings.json", SECRETS_DIR, "runtime_config.json", "logs", "uploads", "extras",
    ".ka_server.lock", STATE_DIR, "__pycache__",
})
_EXCLUDED_PREFIXES = (".r2_",)
_EXCLUDED_SUFFIXES = (".lock", ".tmp")
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")

Progress = Callable[[str, int, int], None]


def state_dir(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_DIR


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


# ── 旧配置 ─────────────────────────────────────────────────────────


def merge_legacy_config(plugin_config: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """旧插件配置叠加 runtime_config.json（与插件版组合根同口径：后者逐键覆盖）。"""
    merged: dict[str, Any] = {
        k: dict(v) if isinstance(v, dict) else v for k, v in plugin_config.items()
    }
    for section, values in runtime.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def legacy_db_filenames(config: dict[str, Any]) -> tuple[str, str]:
    """旧配置里的 SQLite / Milvus 文件名（可能被改过；导入后统一成新版默认名）。"""
    source = config.get("source_store") if isinstance(config.get("source_store"), dict) else {}
    vector = config.get("vector_db") if isinstance(config.get("vector_db"), dict) else {}
    return (
        str(source.get("db_filename") or DB_FILENAME),
        str(vector.get("db_filename") or VECTOR_FILENAME),
    )


# ── 预览 ──────────────────────────────────────────────────────────


def _excluded(name: str) -> bool:
    return (
        name in _EXCLUDED_NAMES
        or name.startswith(_EXCLUDED_PREFIXES)
        or name.endswith(_EXCLUDED_SUFFIXES)
    )


def _tree_stats(path: Path) -> tuple[int, int]:
    if path.is_file():
        return 1, path.stat().st_size
    files = size = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            files += 1
            size += item.stat().st_size
    return files, size


def _count_documents(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            return int(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    except sqlite3.Error:
        return 0


def inspect_legacy_install(selected: str | Path) -> dict[str, Any]:
    """欢迎页「导入前预览」：找到了什么、多少文档、多大。找不到抛 LegacyImportError。"""
    install = locate(selected)
    config = _load_config(install)
    db_name, vector_name = legacy_db_filenames(config)
    entries = [p for p in install.data_dir.iterdir() if not _excluded(p.name)]
    files = size = 0
    for entry in entries:
        f, s = _tree_stats(entry)
        files += f
        size += s
    return {
        "data_dir": str(install.data_dir),
        "config_found": install.config_file is not None,
        "runtime_config_found": install.runtime_config is not None,
        "document_count": _count_documents(install.data_dir / db_name),
        "file_count": files,
        "size_bytes": size,
        "has_database": (install.data_dir / db_name).is_file(),
        "has_vector_store": (install.data_dir / vector_name).exists(),
        "has_graph": (install.data_dir / "lightrag_workspaces").is_dir(),
        "has_secrets": (install.data_dir / SECRETS_DIR).is_dir(),
    }


def _load_config(install: LegacyInstall) -> dict[str, Any]:
    plugin = read_json(install.config_file) if install.config_file else {}
    runtime = read_json(install.runtime_config) if install.runtime_config else {}
    return merge_legacy_config(plugin, runtime)


# ── 复制 ──────────────────────────────────────────────────────────


def _sqlite_copy(source: Path, target: Path) -> None:
    """用 SQLite backup API 复制（带上 WAL 里尚未落盘的内容），源库只读打开。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    uri = f"{source.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


class _Copier:
    def __init__(self, total: int, progress: Progress | None) -> None:
        self.total = total
        self.done = 0
        self._progress = progress

    def tick(self, n: int = 1) -> None:
        self.done += n
        if self._progress is not None:
            self._progress("copying", self.done, self.total)

    def copy_file(self, src: str, dst: str) -> str:
        shutil.copy2(src, dst)
        self.tick()
        return dst


def _copy_payload(
    install: LegacyInstall, payload: Path, config: dict[str, Any], progress: Progress | None
) -> None:
    db_name, vector_name = legacy_db_filenames(config)
    sidecars = {db_name + suffix for suffix in _SQLITE_SIDECARS}
    entries = [
        p for p in sorted(install.data_dir.iterdir())
        if not _excluded(p.name) and p.name not in sidecars
    ]
    copier = _Copier(sum(_tree_stats(p)[0] for p in entries), progress)
    payload.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        name = entry.name
        if name == db_name:
            _sqlite_copy(entry, payload / DB_FILENAME)
            copier.tick()
            continue
        target = payload / (VECTOR_FILENAME if name == vector_name else name)
        if entry.is_dir():
            shutil.copytree(entry, target, copy_function=copier.copy_file, symlinks=True)
        else:
            copier.copy_file(str(entry), str(target))


def _copy_secrets(install: LegacyInstall, target: Path) -> int:
    source = install.data_dir / SECRETS_DIR
    if not source.is_dir():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in source.iterdir():
        if item.is_file() and (item.name == "master.key" or item.name.endswith(".secret")):
            shutil.copy2(item, target / item.name)
            count += item.name.endswith(".secret")
    return count


# ── 路径改写 ──────────────────────────────────────────────────────


def rewrite_legacy_path(value: str, data_dir: Path) -> str | None:
    """旧插件数据目录下的绝对路径 → 新数据目录下的同名路径；不在旧目录下返回 None。

    特征段匹配（正反斜杠都认），因此旧安装在别的机器 / 容器里的绝对路径也能改写。
    """
    normalized = value.replace("\\", "/")
    idx = normalized.find(PATH_MARKER + "/")
    if idx < 0:
        return None
    rest = normalized[idx + len(PATH_MARKER) + 1 :]
    parts = [p for p in PurePosixPath(rest).parts if p not in ("", ".", "..")]
    return str(Path(data_dir).joinpath(*parts))


def rewrite_paths(db_path: Path, payload: Path, data_dir: Path) -> dict[str, int]:
    """改写 staging 库里的绝对路径，使其指向新数据目录。

    - documents.file_path：先按特征段改写前缀；不含特征段但制品包里有原件的，指向制品包原件。
    - zotero_attachments.resolved_path：只改写落在旧插件目录下的（Zotero 自己的目录不动）。
    返回改写计数与「改写后原件仍不存在」的文档数（多为 Zotero 链接模式的外部原件）。
    """
    rewritten = missing = 0
    with closing(sqlite3.connect(db_path)) as db:
        rows = db.execute("SELECT doc_id, file_path FROM documents").fetchall()
        for doc_id, file_path in rows:
            new_path = rewrite_legacy_path(str(file_path or ""), data_dir)
            if new_path is None:
                bundled = payload / "library" / str(doc_id) / "original.pdf"
                if bundled.is_file() and not Path(str(file_path or "")).is_file():
                    new_path = str(data_dir / "library" / str(doc_id) / "original.pdf")
            if new_path is not None and new_path != file_path:
                db.execute(
                    "UPDATE documents SET file_path = ? WHERE doc_id = ?", (new_path, doc_id)
                )
                rewritten += 1
            # 存在性：新数据目录下的路径到 staging 副本里查，外部路径（Zotero 链接）直接查本机。
            final = new_path or str(file_path or "")
            relative = _relative_to(final, data_dir)
            exists = (payload / relative).is_file() if relative else Path(final).is_file()
            missing += 0 if exists else 1
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='zotero_attachments'"
        ).fetchone()
        if table:
            for rowid, resolved in db.execute(
                "SELECT rowid, resolved_path FROM zotero_attachments"
            ).fetchall():
                new_path = rewrite_legacy_path(str(resolved or ""), data_dir)
                if new_path is not None and new_path != resolved:
                    db.execute(
                        "UPDATE zotero_attachments SET resolved_path = ? WHERE rowid = ?",
                        (new_path, rowid),
                    )
                    rewritten += 1
        db.commit()
    return {"paths_rewritten": rewritten, "missing_originals": missing}


def _relative_to(path: str, root: Path) -> Path | None:
    try:
        return Path(path).relative_to(root)
    except ValueError:
        return None


# ── 主流程 ────────────────────────────────────────────────────────


def _guard_directories(install: LegacyInstall, data_dir: Path) -> None:
    source = install.data_dir.resolve()
    target = Path(data_dir).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise LegacyImportError(
            "The old plugin data folder overlaps the current data folder; choose another data "
            "folder before importing."
        )


def _prepare(
    install: LegacyInstall,
    data_dir: Path,
    stage_root: Path,
    config: dict[str, Any],
    progress: Progress | None,
) -> dict[str, Any]:
    """同步部分：复制数据、旧配置与机密，改写路径。"""
    payload = stage_root / PAYLOAD_DIR
    _copy_payload(install, payload, config, progress)
    config_dir = stage_root / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    if install.config_file is not None:
        shutil.copy2(install.config_file, config_dir / PLUGIN_CONFIG_COPY)
    if install.runtime_config is not None:
        shutil.copy2(install.runtime_config, config_dir / RUNTIME_CONFIG_COPY)
    secrets = _copy_secrets(install, stage_root / SECRETS_DIR)
    if progress is not None:
        progress("rewriting", 0, 0)
    db_path = payload / DB_FILENAME
    stats = (
        rewrite_paths(db_path, payload, Path(data_dir))
        if db_path.is_file()
        else {"paths_rewritten": 0, "missing_originals": 0}
    )
    return {**stats, "secrets_found": secrets, "document_count": _count_documents(db_path)}


async def stage_legacy_import(
    selected: str | Path,
    data_dir: Path,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """把旧版插件数据准备成 staging 副本并写 pending 标记；返回摘要与需要确认的事项。

    失败时删除 staging 并抛 LegacyImportError；已有待应用的导入时拒绝（不覆盖）。
    """
    from kacore.legacy_import.secrets import read_legacy_secrets
    from kacore.migration_runner import run_migrations
    from kacore.settings.legacy_keys import translate

    install = locate(selected)
    data_dir = Path(data_dir)
    _guard_directories(install, data_dir)
    root = state_dir(data_dir)
    if (root / PENDING_FILE).exists() or (root / APPLIED_FILE).exists():
        raise LegacyImportError(
            "An import is already waiting to be applied; restart the service to finish it first."
        )
    import_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    stage_root = root / STAGING_DIR / import_id
    config = _load_config(install)
    try:
        stats = await asyncio.to_thread(_prepare, install, data_dir, stage_root, config, progress)
        db_path = stage_root / PAYLOAD_DIR / DB_FILENAME
        if db_path.is_file():
            if progress is not None:
                progress("migrating", 0, 0)
            import aiosqlite

            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                applied = await run_migrations(db)
            stats["migrations_applied"] = len(applied)
        translation = translate(config)
        secrets = await asyncio.to_thread(read_legacy_secrets, stage_root / SECRETS_DIR)
    except LegacyImportError:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - 任何失败都回滚 staging，并给出可读原因
        shutil.rmtree(stage_root, ignore_errors=True)
        raise LegacyImportError(f"Preparing the import failed: {exc}") from exc

    attention = list(translation.attention)
    if stats["secrets_found"] and len(secrets) < stats["secrets_found"]:
        attention.append(
            f"{stats['secrets_found'] - len(secrets)} old secret(s) could not be decrypted; enter "
            "them again in their nodes"
        )
    if stats["missing_originals"]:
        attention.append(
            f"The originals of {stats['missing_originals']} document(s) are not in the old plugin "
            "data folder (usually Zotero linked mode); make sure these files are still available "
            "on this computer"
        )
    if install.config_file is None:
        attention.append("No old plugin settings file was found; settings keep the new defaults")
    summary = {
        "import_id": import_id,
        "source": str(install.data_dir),
        "staged_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **stats,
        "settings_translated": len(translation.values),
        "providers_created": len(translation.providers),
        "secrets_imported": len(secrets),
    }
    marker = {
        "import_id": import_id,
        "stage_root": str(stage_root),
        "summary": summary,
        "attention": attention,
        "dropped": list(translation.dropped),
    }
    write_json(root / PENDING_FILE, marker)
    return marker


__all__ = [
    "APPLIED_FILE",
    "CONFIG_DIR",
    "DB_FILENAME",
    "PAYLOAD_DIR",
    "PENDING_FILE",
    "PLUGIN_CONFIG_COPY",
    "RESULT_FILE",
    "RUNTIME_CONFIG_COPY",
    "SECRETS_DIR",
    "inspect_legacy_install",
    "merge_legacy_config",
    "read_json",
    "rewrite_legacy_path",
    "rewrite_paths",
    "stage_legacy_import",
    "state_dir",
    "write_json",
]
