"""独立应用 CLI 薄壳：``knowledge-arch serve`` 等子命令。

只做参数解析 + 生命周期托管，业务全部委派 ka_core（经 bootstrap 注入）。
status/doctor/passwd 子命令在 Phase 4 补齐。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from typing import Any

from ka_standalone import __version__
from ka_standalone.bootstrap import build_initializer
from ka_standalone.config_bootstrap import load_or_create
from ka_standalone.paths import resolve_data_dir

logger = logging.getLogger("ka_standalone")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-arch",
        description="Knowledge Arch 独立应用：原件中心知识库（WebUI + 对话 Agent）。",
    )
    parser.add_argument("--version", action="version", version=f"knowledge-arch {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动服务（WebUI 常驻，Ctrl+C 退出）")
    serve.add_argument("--data-dir", help="数据目录（默认 ~/.knowledge-arch，或 KR_DATA_DIR）")
    serve.add_argument("--host", help="覆盖 web_console.host（默认 127.0.0.1）")
    serve.add_argument("--port", type=int, help="覆盖 web_console.port（默认 26618）")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "serve":
        return _run_serve(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def _run_serve(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_config, generated_password = load_or_create(data_dir)

    web = raw_config.setdefault("web_console", {})
    web["enabled"] = True  # standalone 本体即 WebUI
    if args.host:
        web["host"] = args.host
    if args.port:
        web["port"] = args.port

    try:
        return asyncio.run(_serve(data_dir, raw_config, generated_password))
    except KeyboardInterrupt:
        return 0


async def _serve(
    data_dir: Any, raw_config: dict[str, Any], generated_password: str | None
) -> int:
    initializer = build_initializer(data_dir, raw_config)
    await initializer.initialize()
    try:
        if not initializer.web_console_running:
            logger.error(
                "Web 控制台未能启动（端口被占用或密码为空）。请检查 %s 或指定 --port。",
                data_dir / "standalone_config.json",
            )
            return 1

        web_cfg = initializer.config.get_web_console_config()
        main_llm = initializer.config.get_main_llm_config()
        logger.info("Knowledge Arch standalone %s 已启动", __version__)
        logger.info(
            "WebUI: http://%s:%s （用户名 %s）", web_cfg.host, web_cfg.port, web_cfg.username
        )
        if generated_password is not None:
            logger.info("首次运行已生成登录密码：%s （保存在 standalone_config.json，可用它登录）",
                        generated_password)
        if not main_llm.configured:
            logger.warning(
                "main_llm 未配置（base_url/model 为空）：问答/图谱等 LLM 能力将降级。"
                "可在设置页或 standalone_config.json 配置任意 OpenAI-compatible 端点。"
            )

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # Windows 事件循环无信号支持
                loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        logger.info("收到退出信号，正在关闭…")
        return 0
    finally:
        await initializer.teardown()


__all__ = ["main"]
