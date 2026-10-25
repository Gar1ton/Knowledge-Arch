"""`python -I -m ka_server serve` —— 本地后端入口（薄壳，零业务）。

启动链：解析目录 → 数据目录独占锁 → AppInitializer.initialize() → 127.0.0.1 上起 HTTP →
打印 `KA_READY port=…` → 等待退出 → 关 HTTP → AppInitializer.teardown()（释放 Milvus Lite 锁）。

token 只经环境变量 `KA_TOKEN` 传入（读后立即从环境中移除），不进命令行、不打印、不落盘。
开发期需要浏览器直连时，用 `KA_DEV_ORIGINS`（逗号分隔）或 `--dev-origin` 追加 CORS 白名单。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ka_server import APP_VERSION
from ka_server.lifecycle import (
    DataDirLock,
    DataDirLockedError,
    announce_ready,
    bound_port,
    install_signal_handlers,
    wait_for_shutdown,
)
from ka_server.paths import AppPaths

if TYPE_CHECKING:
    from kacore.extras.manager import ExtrasManager
    from kacore.settings.store import SettingsStore

logger = logging.getLogger("ka_server")

ENV_TOKEN = "KA_TOKEN"
ENV_DEV_ORIGINS = "KA_DEV_ORIGINS"
LISTEN_HOST = "127.0.0.1"
EXIT_USAGE = 2
EXIT_DATA_DIR_LOCKED = 3
_LOG_FILE = "ka_server.log"
_LEGACY_RUNTIME_CONFIG = "runtime_config.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ka_server", description="Knowledge Arch local backend")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the local HTTP backend")
    serve.add_argument("--port", type=int, default=0, help="0 = let the OS pick a free port")
    serve.add_argument("--data-dir", default=None, help="override the platform data directory")
    serve.add_argument("--parent-pid", type=int, default=None, help="exit when this pid exits")
    serve.add_argument(
        "--uv",
        default=None,
        help="path to the uv executable used to install optional components (default: $KA_UV)",
    )
    serve.add_argument(
        "--dev-origin",
        action="append",
        default=[],
        help="extra CORS origin for development (repeatable)",
    )
    return parser


def configure_logging(paths: AppPaths, *, level: str = "info", keep_days: int = 14) -> None:
    """stderr + 按天滚动的日志文件（保留 keep_days 份）。stdout 只留给 KA_READY 协议行。"""
    numeric = logging.DEBUG if level == "debug" else logging.INFO
    root = logging.getLogger()
    root.setLevel(numeric)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 显式设 handler 级别：日志页的内存 handler 会把 root 调到 DEBUG，终端与文件不跟着刷屏。
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(numeric)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        paths.logs / _LOG_FILE,
        when="midnight",
        backupCount=max(1, keep_days),
        encoding="utf-8",
    )
    file_handler.setLevel(numeric)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def open_settings(paths: AppPaths) -> SettingsStore:
    """settings.json + 加密库（主密钥优先放系统钥匙串）。"""
    from kacore.secret_store import EncryptedSecretStore, system_keyring
    from kacore.settings.store import SettingsStore

    secrets = EncryptedSecretStore(paths.secrets, keyring_backend=system_keyring())
    return SettingsStore.open(paths.settings_file, secrets)


def migrate_legacy_runtime_config(paths: AppPaths, settings: SettingsStore) -> bool:
    """数据目录里只有旧版 runtime_config.json、还没有 settings.json 时，就地翻译一次。

    旧文件保留不动（只读来源）；settings.json 一旦存在就不再触发。
    """
    legacy = paths.data / _LEGACY_RUNTIME_CONFIG
    if settings.persisted or not legacy.exists():
        return False
    from kacore.settings.legacy_keys import apply_translation, translate

    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read the old runtime_config.json; skipping migration: %s", exc)
        return False
    translation = translate(raw if isinstance(raw, dict) else {})
    apply_translation(settings, translation)
    settings.save()
    logger.info(
        "Migrated %d setting(s) from the old runtime_config.json; dropped %d: %s",
        len(translation.values),
        len(translation.dropped),
        ", ".join(translation.dropped) or "none",
    )
    for note in translation.attention:
        logger.warning("Migration note: %s", note)
    return True


def open_extras(paths: AppPaths, uv_path: str | None) -> ExtrasManager:
    """可选组件目录（`<data>/extras/`）：构造管理器并把已装组件加入 sys.path。

    必须在组合根 initialize() 之前调用——各子系统按「模块是否可 import」决定是否启用。
    """
    from kacore.extras.manager import ExtrasManager
    from kacore.extras.uv import UvInstaller, locate_uv

    manager = ExtrasManager(paths.extras, UvInstaller(locate_uv(uv_path)))
    manager.activate()
    return manager


def dev_origins(cli_values: Sequence[str]) -> list[str]:
    env_values = [v.strip() for v in os.environ.get(ENV_DEV_ORIGINS, "").split(",")]
    return [v for v in (*cli_values, *env_values) if v]


async def serve(args: argparse.Namespace, token: str) -> int:
    from aiohttp import web

    from kacore.app_initializer import AppInitializer
    from web.server import DEFAULT_ALLOWED_ORIGINS, build_app

    paths = AppPaths.resolve(args.data_dir)
    paths.ensure()
    settings = open_settings(paths)
    configure_logging(
        paths,
        level=str(settings.get("prefs.diagnostics.log_level")),
        keep_days=int(settings.get("prefs.diagnostics.log_keep_days")),
    )
    logger.info("Knowledge Arch backend %s, data dir: %s", APP_VERSION, paths.data)

    lock = DataDirLock(paths.lock_file)
    try:
        lock.acquire()
    except DataDirLockedError as exc:
        logger.error("DataDirLockedError: %s", exc)
        return EXIT_DATA_DIR_LOCKED

    migrate_legacy_runtime_config(paths, settings)
    extras = open_extras(paths, args.uv)
    stop = asyncio.Event()
    install_signal_handlers(stop)
    app_init = AppInitializer(settings, paths.data, extras=extras)
    runner: web.AppRunner | None = None
    try:
        await app_init.initialize_or_rollback(raise_after_recovery=False)
        app = build_app(
            api_provider=lambda: app_init.api,
            upload_dir=paths.uploads,
            token=token,
            allowed_origins=(*DEFAULT_ALLOWED_ORIGINS, *dev_origins(args.dev_origin)),
            shutdown_callback=stop.set,
            log_handler=app_init.log_handler,
        )
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, LISTEN_HOST, args.port)
        await site.start()
        announce_ready(bound_port(site))
        reason = await wait_for_shutdown(stop, args.parent_pid)
        logger.info("shutdown: %s", reason)
    finally:
        if runner is not None:
            await runner.cleanup()
        await app_init.teardown()
        lock.release()
        logger.info("shutdown complete")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.pop(ENV_TOKEN, "")
    if not token:
        print(f"{ENV_TOKEN} environment variable is required", file=sys.stderr)
        return EXIT_USAGE
    return asyncio.run(serve(args, token))


if __name__ == "__main__":
    sys.exit(main())
