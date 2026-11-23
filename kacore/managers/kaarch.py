"""一键导出 / 导入本机的设置、密钥与知识库：`.kaarch` 文件（managers 层·3.0.0）。

为何存在：用户 11-19 要求「支持一键导出与导入本地的设置 / 带加密 key 的设置 / collection 与构筑，
方便备份与设备转移」。11-20 定：本机库不空时导入弹框选「完全覆盖 / 合并」，两种都做。

文件是一个 zip：
- manifest.json   格式与版本、勾了哪几样（settings / secrets / library）、
                  payload 文件清单与 sha256、Embedding 指纹、
                  数量（文档、集合、笔记、对话）、密钥条数。
- settings.json   可携设置（`SettingsStore.portable_document()`：不含机密与本机路径）。
- secrets.json    口令加密的 {密钥名: 值}（`utils/passphrase_box`）；不设口令就没有这个文件。
- payload/…       与 R2 快照同构的库存（`managers/data_inventory.build_inventory`）。

导入：
- 完全覆盖：payload 解进 R2 的恢复暂存目录、写待恢复标记，重启时由
  `r2_restore.apply_pending_restore` 替换（失败回滚）；设置随恢复合并，密钥当场写入
  （原来的密钥文件先另存一份）。
- 合并：见 `managers/kaarch_merge.py`。
导出与导入都在后台任务里跑，`job()` 给进度。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from kacore.managers.data_inventory import build_inventory, portable_settings, write_json_atomic
from kacore.managers.r2_backup_manifest import check_sqlite_integrity, hash_inventory, sha256_file
from kacore.utils.passphrase_box import open_box, seal

if TYPE_CHECKING:
    from kacore.config import Config
    from kacore.managers.kaarch_merge import LibraryWriter
    from kacore.repository.source_store.base import SourceDocumentStore
    from kacore.settings.base import SecretStore
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("Kaarch")

FORMAT = "kaarch"
SCHEMA_VERSION = 1
SUFFIX = ".kaarch"
PART_SETTINGS, PART_SECRETS, PART_LIBRARY = "settings", "secrets", "library"
PARTS = (PART_SETTINGS, PART_SECRETS, PART_LIBRARY)
MODE_OVERWRITE, MODE_MERGE = "overwrite", "merge"
# 与 R2 恢复共用的暂存目录与标记（r2_backup_manager 的 _STAGING_DIR / _PENDING_RESTORE）。
_STAGING_DIR = ".r2_restore_staging"
_PENDING_RESTORE = ".r2_pending_restore.json"
_WORK_DIR = ".kaarch_work"
_SECRETS_BACKUP = ".kaarch_secrets_backup"
# 已经是压缩格式的文件不再压（PDF、图片）；其余 deflate。
_STORED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gz", ".zip"}
# 导入后等多久再重启（秒），让 HTTP 响应先回到界面（假设值）。
_RELOAD_DELAY = 1.0


@dataclass
class KaarchJob:
    job_id: str
    action: str
    status: str = "running"
    stage: str = "prepare"
    progress: int = 0
    detail: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: _now())
    finished_at: str = ""

    def finish(self, status: str, *, error: str = "", result: dict[str, Any] | None = None) -> None:
        self.status, self.error, self.result = status, error, result or {}
        self.progress = 100 if status == "success" else self.progress
        self.finished_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KaarchManager:
    def __init__(
        self,
        *,
        data_dir: Path,
        db_path: Path,
        source_store: SourceDocumentStore,
        config: Config,
        settings: SettingsStore | None,
        secrets: SecretStore | None,
        embedding_fingerprint: str | None = None,
        quiesce_indexes: Callable[[], Awaitable[None]] | None = None,
        reload_callback: Callable[[], Awaitable[None]] | None = None,
        writer: LibraryWriter | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._db_path = db_path
        self._store = source_store
        self._config = config
        self._settings = settings
        self._secrets = secrets
        self._app_version = ""
        self._fingerprint = embedding_fingerprint or ""
        self._quiesce = quiesce_indexes
        self._reload = reload_callback
        self._writer = writer
        self._job: KaarchJob | None = None
        self._task: asyncio.Task[None] | None = None
        self._reload_task: asyncio.Task[None] | None = None

    def attach_writer(self, writer: LibraryWriter) -> None:
        """合并要用 api 的建集合 / 入库 / 建笔记（组合根在 api 构造后注入）。"""
        self._writer = writer

    # ── 任务 ────────────────────────────────────────────────

    def job(self) -> dict[str, Any] | None:
        return self._job.to_dict() if self._job else None

    def _start(self, action: str, run: Callable[[KaarchJob], Awaitable[dict[str, Any]]]
               ) -> dict[str, Any]:
        if self._job is not None and self._job.status == "running":
            raise RuntimeError("an export or import is already running")
        job = KaarchJob(uuid.uuid4().hex[:12], action)
        self._job = job

        async def runner() -> None:
            try:
                job.finish("success", result=await run(job))
            except Exception as exc:  # noqa: BLE001 - 失败写进任务给界面看
                logger.error("%s failed: %s", action, exc, exc_info=True)
                job.finish("error", error=str(exc))

        self._task = asyncio.create_task(runner())
        return job.to_dict()

    # ── 导出 ────────────────────────────────────────────────

    def start_export(self, folder: str, parts: list[str], passphrase: str = "",
                     app_version: str = "") -> dict[str, Any]:
        self._app_version = app_version
        target = Path(folder).expanduser()
        chosen = [p for p in PARTS if p in set(parts)]
        if not target.is_dir():
            raise ValueError(f"folder does not exist: {folder}")
        if not chosen:
            raise ValueError("choose at least one of settings, keys, library")
        if PART_SECRETS in chosen and not passphrase:
            raise ValueError("set a passphrase to include keys")
        return self._start("export", lambda job: self.export(target, chosen, passphrase, job))

    async def export(self, folder: Path, parts: list[str], passphrase: str,
                     job: KaarchJob) -> dict[str, Any]:
        work = self._data_dir / _WORK_DIR / job.job_id
        dest = folder / f"knowledge-arch_{datetime.now().strftime('%Y%m%d-%H%M')}{SUFFIX}"
        try:
            manifest: dict[str, Any] = {
                "format": FORMAT, "schema_version": SCHEMA_VERSION,
                "app_version": self._app_version, "created_at": _now(), "parts": parts,
                "embedding_fingerprint": self._fingerprint, "files": [], "counts": {},
                "secret_count": 0,
            }
            members: list[tuple[Path, str]] = []
            if PART_LIBRARY in parts:
                job.stage, job.progress = "inventory", 5
                if self._quiesce is not None:
                    await self._quiesce()
                payload = work / "payload"
                await build_inventory(payload, db_path=self._db_path, data_dir=self._data_dir,
                                      source_store=self._store, config=self._config,
                                      settings=self._settings)
                shutil.rmtree(payload / "config", ignore_errors=True)
                job.stage, job.progress = "hash", 30
                entries = await asyncio.to_thread(hash_inventory, payload)
                manifest["files"] = [{"path": e.path, "size": e.size, "sha256": e.sha256}
                                     for e in entries]
                manifest["counts"] = await asyncio.to_thread(
                    library_counts, payload / "database" / "knowledge_repository.db")
                members += [(payload / e.path, f"payload/{e.path}") for e in entries]
            work.mkdir(parents=True, exist_ok=True)
            if PART_SETTINGS in parts:
                write_json_atomic(work / "settings.json",
                                  portable_settings(self._config, self._settings))
                members.append((work / "settings.json", "settings.json"))
            if PART_SECRETS in parts:
                values = self._secret_values()
                manifest["secret_count"] = len(values)
                box = await asyncio.to_thread(seal, values, passphrase)
                write_json_atomic(work / "secrets.json", box)
                members.append((work / "secrets.json", "secrets.json"))
            write_json_atomic(work / "manifest.json", manifest)
            members.insert(0, (work / "manifest.json", "manifest.json"))
            job.stage = "write"
            await asyncio.to_thread(_write_zip, dest, members, job)
        finally:
            await asyncio.to_thread(shutil.rmtree, work, True)
        return {"path": str(dest), "size": dest.stat().st_size, "parts": parts,
                "files": len(manifest["files"]), "counts": manifest["counts"]}

    # ── 读清单 ──────────────────────────────────────────────

    async def inspect(self, path: str) -> dict[str, Any]:
        """导入前只读：文件里有什么、与本机比哪里不同、本机库空不空。什么都不改。"""
        archive = Path(path).expanduser()
        manifest = await asyncio.to_thread(read_manifest, archive)
        settings_diff = 0
        if PART_SETTINGS in manifest["parts"]:
            incoming = await asyncio.to_thread(_read_json_member, archive, "settings.json")
            current = portable_settings(self._config, self._settings).get("values", {})
            settings_diff = sum(1 for k, v in (incoming.get("values") or {}).items()
                                if current.get(k) != v)
        local_docs = len(await self._store.list_documents())
        fingerprint = str(manifest.get("embedding_fingerprint") or "")
        return {
            "path": str(archive), "size": archive.stat().st_size,
            "app_version": manifest.get("app_version", ""),
            "created_at": manifest.get("created_at", ""), "parts": manifest["parts"],
            "counts": manifest.get("counts") or {}, "secret_count": manifest.get("secret_count", 0),
            "settings_diff": settings_diff,
            "embedding_same": bool(fingerprint) and fingerprint == self._fingerprint,
            "local_documents": local_docs, "local_empty": local_docs == 0,
        }

    # ── 导入 ────────────────────────────────────────────────

    def start_import(self, path: str, mode: str, passphrase: str = "") -> dict[str, Any]:
        archive = Path(path).expanduser()
        manifest = read_manifest(archive)
        secrets = None
        if PART_SECRETS in manifest["parts"] and passphrase:
            # 口令先验：错了当场报，什么都不动。
            secrets = open_box(_read_json_member(archive, "secrets.json"), passphrase)
        if mode == MODE_OVERWRITE:
            return self._start("import_overwrite",
                               lambda job: self.import_overwrite(archive, manifest, secrets, job))
        if mode == MODE_MERGE:
            if self._writer is None:
                raise RuntimeError("merge import is not available")
            from kacore.managers.kaarch_merge import merge_archive

            writer = self._writer
            return self._start("import_merge", lambda job: merge_archive(
                archive, manifest, secrets, job, store=self._store, writer=writer,
                settings=self._settings, secret_store=self._secrets,
                work=self._data_dir / _WORK_DIR / job.job_id))
        raise ValueError(f"unknown import mode: {mode}")

    async def import_overwrite(self, archive: Path, manifest: dict[str, Any],
                               secrets: dict[str, str] | None, job: KaarchJob) -> dict[str, Any]:
        restart = False
        if PART_LIBRARY in manifest["parts"]:
            stage_root = self._data_dir / _STAGING_DIR / f"kaarch-{job.job_id}"
            job.stage, job.progress = "extract", 5
            await asyncio.to_thread(_stage_payload, archive, manifest, stage_root, job)
            if PART_SETTINGS in manifest["parts"]:
                write_json_atomic(stage_root / "payload" / "config" / "portable_settings.json",
                                  _read_json_member(archive, "settings.json"))
            write_json_atomic(self._data_dir / _PENDING_RESTORE, {
                "snapshot_id": f"kaarch-{job.job_id}", "stage_root": str(stage_root),
                "auto_restart": True, "created_at": _now(),
            })
            restart = True
        elif PART_SETTINGS in manifest["parts"] and self._settings is not None:
            self._settings.merge_portable(_read_json_member(archive, "settings.json"))
            restart = True
        written = self._write_secrets(secrets, job.job_id, only_missing=False)
        if restart and self._reload is not None:
            self._reload_task = asyncio.create_task(self._delayed_reload())
        return {"restart": restart, "secrets_written": written,
                "documents": (manifest.get("counts") or {}).get("documents", 0)}

    # ── 内部 ────────────────────────────────────────────────

    async def _delayed_reload(self) -> None:
        """稍等再软重启，让「导入完成、正在重启」先回到界面；重启时应用暂存的恢复。"""
        await asyncio.sleep(_RELOAD_DELAY)
        assert self._reload is not None
        await self._reload()

    def _secret_values(self) -> dict[str, str]:
        if self._secrets is None:
            return {}
        values = {name: self._secrets.get_secret(name) for name in self._secrets.list_names()}
        return {k: v for k, v in values.items() if v}

    def _write_secrets(self, values: dict[str, str] | None, tag: str, *,
                       only_missing: bool) -> int:
        """写入导入的密钥；完全覆盖前把本机原来的密钥文件另存一份（仍是本机主密钥加密的密文）。"""
        if not values or self._secrets is None:
            return 0
        source = self._data_dir / "secrets"
        if not only_missing and source.is_dir():
            shutil.copytree(source, self._data_dir / _SECRETS_BACKUP / tag, dirs_exist_ok=True)
        count = 0
        for name, value in values.items():
            if only_missing and self._secrets.has_secret(name):
                continue
            self._secrets.set_secret(name, value)
            count += 1
        return count


# ── 文件格式 ────────────────────────────────────────────────


def read_manifest(archive: Path) -> dict[str, Any]:
    """读并检查清单；不是 .kaarch / 版本不认识 / 缺文件时抛 ValueError。"""
    if not archive.is_file():
        raise ValueError(f"file not found: {archive}")
    try:
        manifest = _read_json_member(archive, "manifest.json")
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("this is not a Knowledge Arch export (.kaarch)") from exc
    version = int(manifest.get("schema_version") or 0)
    if manifest.get("format") != FORMAT or version != SCHEMA_VERSION:
        raise ValueError("unsupported .kaarch version")
    manifest["parts"] = [p for p in manifest.get("parts") or [] if p in PARTS]
    return manifest


def library_counts(db_path: Path) -> dict[str, int]:
    """导出时记下的数量（只读快照库）。"""
    queries = {
        "documents": "SELECT COUNT(*) FROM documents",
        "local_documents": "SELECT COUNT(*) FROM documents WHERE origin = 'local'",
        "collections": "SELECT COUNT(*) FROM collections",
        "notes": "SELECT COUNT(*) FROM scoped_notes",
        "conversations": "SELECT COUNT(DISTINCT conversation_id) FROM chat_history",
    }
    out: dict[str, int] = {}
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        for key, sql in queries.items():
            try:
                out[key] = int(db.execute(sql).fetchone()[0])
            except sqlite3.Error:
                out[key] = 0
    return out


def _read_json_member(archive: Path, name: str) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as zf:
        value = json.loads(zf.read(name).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not a JSON object")
    return value


def _write_zip(dest: Path, members: list[tuple[Path, str]], job: KaarchJob) -> None:
    tmp = dest.with_name(dest.name + ".part")
    total = max(len(members), 1)
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
        for i, (source, arcname) in enumerate(members, start=1):
            method = zipfile.ZIP_STORED if source.suffix.lower() in _STORED \
                else zipfile.ZIP_DEFLATED
            zf.write(source, arcname, compress_type=method)
            job.progress = 35 + int(60 * i / total)
    tmp.replace(dest)


def _stage_payload(archive: Path, manifest: dict[str, Any], stage_root: Path,
                   job: KaarchJob) -> None:
    """payload 解进暂存目录并逐个核对 sha256；数据库再做完整性检查。任何一步失败都删掉暂存。"""
    if stage_root.exists():
        shutil.rmtree(stage_root)
    payload = stage_root / "payload"
    files = manifest.get("files") or []
    need = sum(int(f.get("size") or 0) for f in files)
    if shutil.disk_usage(stage_root.parent if stage_root.parent.exists()
                         else archive.parent).free < need:
        raise RuntimeError(f"not enough disk space: {need} bytes needed")
    try:
        with zipfile.ZipFile(archive) as zf:
            for i, entry in enumerate(files, start=1):
                rel = PurePosixPath(str(entry["path"]))
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError(f"unsafe path in archive: {rel}")
                target = payload.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(f"payload/{rel.as_posix()}") as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                if sha256_file(target) != entry["sha256"]:
                    raise ValueError(f"file check failed: {rel}")
                job.progress = 5 + int(80 * i / max(len(files), 1))
        db = payload / "database" / "knowledge_repository.db"
        if not db.exists():
            raise ValueError("the export has no knowledge_repository.db")
        check_sqlite_integrity(db)
    except BaseException:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "FORMAT",
    "MODE_MERGE",
    "MODE_OVERWRITE",
    "PARTS",
    "KaarchJob",
    "KaarchManager",
    "library_counts",
    "read_manifest",
]
