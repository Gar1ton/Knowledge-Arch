"""把已准备好的旧版数据换入数据目录（在数据库与索引连接建立之前调用），以及提交 / 回滚。

与 R2 恢复同一套「pending → applied → commit | rollback」协议：
- apply：payload 顶层条目逐个换入，被替换的原内容（及 SQLite 旁路文件）移进 rollback 目录；
  settings.json 与机密目录先整体备份，再写入翻译后的设置、旧版机密；
- commit（初始化成功）：删除 rollback 与 staging，写 result.json（status=ok）；
- rollback（初始化失败）：按记录逆序恢复被替换的内容与设置 / 机密，写 result.json（status=failed）。
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kacore.legacy_import.staging import (
    APPLIED_FILE,
    CONFIG_DIR,
    PAYLOAD_DIR,
    PENDING_FILE,
    PLUGIN_CONFIG_COPY,
    RESULT_FILE,
    RUNTIME_CONFIG_COPY,
    SECRETS_DIR,
    merge_legacy_config,
    read_json,
    state_dir,
    write_json,
)

if TYPE_CHECKING:
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("LegacyImport")

_ROLLBACK_DIR = "rollback"
_SETTINGS_BACKUP = "_settings.json"
_SECRETS_BACKUP = "_secrets"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _apply_settings(stage_root: Path, settings: SettingsStore) -> int:
    """旧配置翻译 → SettingsStore；旧版机密 → 加密库。返回导入的机密数。"""
    from kacore.legacy_import.secrets import read_legacy_secrets
    from kacore.settings.legacy_keys import apply_translation, translate

    config_dir = stage_root / CONFIG_DIR
    merged = merge_legacy_config(
        read_json(config_dir / PLUGIN_CONFIG_COPY), read_json(config_dir / RUNTIME_CONFIG_COPY)
    )
    secrets = read_legacy_secrets(stage_root / SECRETS_DIR)
    imported = 0
    for name, value in secrets.items():
        try:
            settings.secrets.set_secret(name, value)
            imported += 1
        except ValueError:
            logger.warning("Skipping an old secret that cannot be written: %s", name)
    apply_translation(settings, translate(merged))
    return imported


def apply_pending_legacy_import(data_dir: Path, settings: SettingsStore) -> bool:
    """有 pending 标记时把 staging 换入；没有返回 False。失败时已换入的部分原样撤回再上抛。"""
    root = state_dir(data_dir)
    marker = read_json(root / PENDING_FILE)
    if not marker:
        return False
    import_id = str(marker.get("import_id") or "")
    stage_root = Path(str(marker.get("stage_root") or ""))
    payload = stage_root / PAYLOAD_DIR
    if not import_id or not payload.is_dir():
        (root / PENDING_FILE).unlink(missing_ok=True)
        write_json(root / RESULT_FILE, {**marker, (
            "status"
        ): "failed", "error": "staging is missing"})
        return False

    rollback = root / _ROLLBACK_DIR / import_id
    if rollback.exists():
        shutil.rmtree(rollback)
    rollback.mkdir(parents=True)
    settings_file = Path(data_dir) / "settings.json"
    secrets_dir = Path(data_dir) / SECRETS_DIR
    had_settings = settings_file.is_file()
    had_secrets = secrets_dir.is_dir()
    if had_settings:
        shutil.copy2(settings_file, rollback / _SETTINGS_BACKUP)
    if had_secrets:
        shutil.copytree(secrets_dir, rollback / _SECRETS_BACKUP)

    applied: list[dict[str, str]] = []
    try:
        for source in sorted(payload.iterdir()):
            names = [source.name, *(source.name + s for s in _SQLITE_SIDECARS)]
            for name in names:
                target = Path(data_dir) / name
                if target.exists() or target.is_symlink():
                    old = rollback / f"{len(applied):03d}-{name}"
                    shutil.move(str(target), str(old))
                    applied.append({"target": str(target), "old": str(old)})
            target = Path(data_dir) / source.name
            shutil.move(str(source), str(target))
            applied.append({"target": str(target), "old": ""})
        secrets_imported = _apply_settings(stage_root, settings)
    except Exception:
        logger.error("Swapping in old data failed; rolling back what was swapped in", exc_info=True)
        _restore(Path(data_dir), applied, rollback, had_settings, had_secrets)
        shutil.rmtree(rollback, ignore_errors=True)
        raise

    write_json(
        root / APPLIED_FILE,
        {
            **marker,
            "rollback": str(rollback),
            "applied": applied,
            "had_settings": had_settings,
            "had_secrets": had_secrets,
            "secrets_imported": secrets_imported,
        },
    )
    (root / PENDING_FILE).unlink(missing_ok=True)
    logger.info((
        "Old plugin data swapped into the data folder (%s); waiting for startup to confirm"
    ), import_id)
    return True


def _restore(
    data_dir: Path,
    applied: list[dict[str, Any]],
    rollback: Path,
    had_settings: bool,
    had_secrets: bool,
) -> None:
    for row in reversed(applied):
        target = Path(str(row.get("target") or ""))
        old = str(row.get("old") or "")
        if not old:
            if target.exists() or target.is_symlink():
                _remove(target)
            continue
        if Path(old).exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                _remove(target)
            shutil.move(old, str(target))
    settings_file = data_dir / "settings.json"
    if had_settings and (rollback / _SETTINGS_BACKUP).is_file():
        shutil.copy2(rollback / _SETTINGS_BACKUP, settings_file)
    elif not had_settings:
        settings_file.unlink(missing_ok=True)
    secrets_dir = data_dir / SECRETS_DIR
    if secrets_dir.exists():
        shutil.rmtree(secrets_dir)
    if had_secrets and (rollback / _SECRETS_BACKUP).is_dir():
        shutil.copytree(rollback / _SECRETS_BACKUP, secrets_dir)


def commit_applied_legacy_import(data_dir: Path) -> bool:
    """初始化成功后调用：清理 rollback 与 staging，记录结果。没有待确认的导入返回 False。"""
    root = state_dir(data_dir)
    applied = read_json(root / APPLIED_FILE)
    if not applied:
        return False
    shutil.rmtree(Path(str(applied.get("rollback") or "")), ignore_errors=True)
    shutil.rmtree(Path(str(applied.get("stage_root") or "")), ignore_errors=True)
    write_json(
        root / RESULT_FILE,
        {
            "status": "ok",
            "import_id": applied.get("import_id"),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "summary": {
                **(applied.get("summary") or {}),
                "secrets_imported": applied.get("secrets_imported", 0),
            },
            "attention": applied.get("attention") or [],
            "dropped": applied.get("dropped") or [],
        },
    )
    (root / APPLIED_FILE).unlink(missing_ok=True)
    logger.info("Old plugin data import finished (%s)", applied.get("import_id"))
    return True


def rollback_applied_legacy_import(data_dir: Path, *, error: str = "") -> bool:
    """初始化失败后调用：恢复导入前的数据目录、设置与机密。没有待确认的导入返回 False。"""
    root = state_dir(data_dir)
    applied = read_json(root / APPLIED_FILE)
    if not applied:
        return False
    rollback = Path(str(applied.get("rollback") or ""))
    _restore(
        Path(data_dir),
        list(applied.get("applied") or []),
        rollback,
        bool(applied.get("had_settings")),
        bool(applied.get("had_secrets")),
    )
    shutil.rmtree(rollback, ignore_errors=True)
    shutil.rmtree(Path(str(applied.get("stage_root") or "")), ignore_errors=True)
    write_json(
        root / RESULT_FILE,
        {
            "status": "failed",
            "import_id": applied.get("import_id"),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "error": error or (
                "The service failed to start after the import; the data from before the import was "
                "restored."
            ),
            "summary": applied.get("summary") or {},
            "attention": applied.get("attention") or [],
            "dropped": applied.get("dropped") or [],
        },
    )
    (root / APPLIED_FILE).unlink(missing_ok=True)
    logger.warning("Old plugin data import rolled back (%s)", applied.get("import_id"))
    return True


def legacy_import_state(data_dir: Path) -> dict[str, Any]:
    """欢迎页轮询用：待应用的导入（pending）与最近一次结果（result）。"""
    root = state_dir(data_dir)
    pending = read_json(root / PENDING_FILE) or read_json(root / APPLIED_FILE)
    return {
        "pending": (
            {
                "import_id": pending.get("import_id"),
                "summary": pending.get("summary") or {},
                "attention": pending.get("attention") or [],
                "dropped": pending.get("dropped") or [],
            }
            if pending
            else None
        ),
        "result": read_json(root / RESULT_FILE) or None,
    }


def discard_pending_legacy_import(data_dir: Path) -> bool:
    """放弃尚未换入的导入（删除 staging 与 pending 标记）。"""
    root = state_dir(data_dir)
    marker = read_json(root / PENDING_FILE)
    if not marker:
        return False
    shutil.rmtree(Path(str(marker.get("stage_root") or "")), ignore_errors=True)
    (root / PENDING_FILE).unlink(missing_ok=True)
    return True


__all__ = [
    "apply_pending_legacy_import",
    "commit_applied_legacy_import",
    "discard_pending_legacy_import",
    "legacy_import_state",
    "rollback_applied_legacy_import",
]
