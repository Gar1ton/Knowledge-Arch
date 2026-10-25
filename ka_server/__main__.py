"""`python -I -m ka_server serve` —— 本地后端入口（薄壳，零业务）。

启动链：解析目录 → 数据目录独占锁 → AppInitializer.initialize() → 127.0.0.1 上起 HTTP →
打印 `KA_READY port=…` → 等待退出 → 关 HTTP → AppInitializer.teardown()（释放 Milvus Lite 锁）。

token 只经环境变量 `KA_TOKEN` 传入（读后立即从环境中移除），不进命令行、不打印、不落盘。
开发期需要浏览器直连时，用 `KA_DEV_ORIGINS`（逗号分隔）或 `--dev-origin` 追加 CORS 白名单。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
from collections.abc import Sequence

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

logger = logging.getLogger("ka_server")

ENV_TOKEN = "KA_TOKEN"
ENV_DEV_ORIGINS = "KA_DEV_ORIGINS"
LISTEN_HOST = "127.0.0.1"
EXIT_USAGE = 2
EXIT_DATA_DIR_LOCKED = 3
_LOG_FILE = "ka_server.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ka_server", description="Knowledge Arch local backend")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the local HTTP backend")
    serve.add_argument("--port", type=int, default=0, help="0 = let the OS pick a free port")
    serve.add_argument("--data-dir", default=None, help="override the platform data directory")
    serve.add_argument("--parent-pid", type=int, default=None, help="exit when this pid exits")
    serve.add_argument(
        "--dev-origin",
        action="append",
        default=[],
        help="extra CORS origin for development (repeatable)",
    )
    return parser


def configure_logging(paths: AppPaths) -> None:
    """stderr + 滚动文件。stdout 只留给 KA_READY 协议行。"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 显式设 handler 级别：日志页的内存 handler 会把 root 调到 DEBUG，终端与文件不跟着刷屏。
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.INFO)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)
    file_handler = logging.handlers.RotatingFileHandler(
        paths.logs / _LOG_FILE,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def dev_origins(cli_values: Sequence[str]) -> list[str]:
    env_values = [v.strip() for v in os.environ.get(ENV_DEV_ORIGINS, "").split(",")]
    return [v for v in (*cli_values, *env_values) if v]


async def serve(args: argparse.Namespace, token: str) -> int:
    from aiohttp import web

    from kacore.app_initializer import AppInitializer
    from web.server import DEFAULT_ALLOWED_ORIGINS, build_app

    paths = AppPaths.resolve(args.data_dir)
    paths.ensure()
    configure_logging(paths)
    logger.info("Knowledge Arch backend %s, data dir: %s", APP_VERSION, paths.data)

    lock = DataDirLock(paths.lock_file)
    try:
        lock.acquire()
    except DataDirLockedError as exc:
        logger.error("DataDirLockedError: %s", exc)
        return EXIT_DATA_DIR_LOCKED

    stop = asyncio.Event()
    install_signal_handlers(stop)
    app_init = AppInitializer({}, paths.data)
    runner: web.AppRunner | None = None
    try:
        await app_init.initialize()
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
