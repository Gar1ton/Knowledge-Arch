"""独立应用 CLI 薄壳：``knowledge-arch serve|status|doctor|passwd``。

只做参数解析 + 生命周期托管，业务全部委派 kacore（经 bootstrap 注入）。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import platform
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from standalone import __version__
from standalone.bootstrap import build_initializer
from standalone.config_bootstrap import CONFIG_FILENAME, load_or_create
from standalone.paths import resolve_data_dir

logger = logging.getLogger("standalone")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-arch",
        description="Knowledge Arch 独立应用：原件中心知识库（WebUI + 对话 Agent）。",
    )
    parser.add_argument("--version", action="version", version=f"knowledge-arch {__version__}")
    sub = parser.add_subparsers(dest="command")

    def _add_data_dir(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", help="数据目录（默认 ~/.knowledge-arch，或 KR_DATA_DIR）")

    serve = sub.add_parser("serve", help="启动服务（WebUI 常驻，Ctrl+C 退出）")
    _add_data_dir(serve)
    serve.add_argument("--host", help="覆盖 web_console.host（默认 127.0.0.1）")
    serve.add_argument("--port", type=int, help="覆盖 web_console.port（默认 26618）")

    status = sub.add_parser("status", help="打印数据目录/配置/存储文件概览")
    _add_data_dir(status)

    doctor = sub.add_parser("doctor", help="环境体检：依赖、平台限制、外部命令")
    _add_data_dir(doctor)

    passwd = sub.add_parser("passwd", help="修改 WebUI 登录密码（写入 standalone_config.json）")
    _add_data_dir(passwd)
    passwd.add_argument("--set", dest="new_password", help="非交互直接指定新密码")
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
    if args.command == "status":
        return _run_status(args)
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "passwd":
        return _run_passwd(args)
    parser.error(f"unknown command: {args.command}")
    return 2


# ── status / doctor / passwd ─────────────────────────────────


def _fmt_size(path: Path) -> str:
    if not path.exists():
        return "未创建"
    size = path.stat().st_size
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return str(size)


def _run_status(args: argparse.Namespace) -> int:
    from kacore.config import Config
    from kacore.runtime_config import RuntimeConfigStore

    data_dir = resolve_data_dir(args.data_dir)
    config_path = data_dir / CONFIG_FILENAME
    raw: dict[str, Any] = {}
    if config_path.exists():
        with contextlib.suppress(Exception):
            raw = json.loads(config_path.read_text(encoding="utf-8"))
    merged = RuntimeConfigStore(data_dir / "runtime_config.json").merged_with(raw)
    cfg = Config(merged)
    web = cfg.get_web_console_config()
    main_llm = cfg.get_main_llm_config()

    config_state = "✓" if config_path.exists() else "✗ 未初始化（首次 serve 自动生成）"
    print(f"🧩 Knowledge Arch standalone {__version__}")
    print(f"· 数据目录      : {data_dir}{'' if data_dir.exists() else '（未创建）'}")
    print(f"· 配置文件      : {config_path.name} {config_state}")
    print(f"· 主数据库      : {_fmt_size(data_dir / cfg.get_source_store_config().db_filename)}")
    print(f"· 向量库        : {_fmt_size(data_dir / cfg.get_vector_db_config().db_filename)}")
    print(f"· runtime 配置  : {'✓' if (data_dir / 'runtime_config.json').exists() else '—'}")
    print(f"· WebUI         : http://{web.host}:{web.port} （用户名 {web.username}）")
    llm_state = (
        f"{main_llm.base_url}  model={main_llm.model}"
        if main_llm.configured
        else "未配置（LLM 能力降级）"
    )
    print(f"· 主 LLM        : {llm_state}")
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    from kacore.capabilities import module_available

    data_dir = resolve_data_dir(args.data_dir)
    failures = 0

    def check(label: str, ok: bool, hint: str = "", *, critical: bool = False) -> None:
        nonlocal failures
        mark = "✓" if ok else ("✗" if critical else "⚠")
        line = f" {mark} {label}"
        if not ok and hint:
            line += f" —— {hint}"
        print(line)
        if not ok and critical:
            failures += 1

    print("🩺 Knowledge Arch doctor")
    check(
        f"Python {platform.python_version()}",
        sys.version_info >= (3, 10),
        "需要 Python >= 3.10",
        critical=True,
    )
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check(f"数据目录可写（{data_dir}）", True)
    except OSError as exc:
        check(f"数据目录可写（{data_dir}）", False, str(exc), critical=True)

    check("aiohttp / aiosqlite / PyMuPDF（必装依赖）",
          all(module_available(m) for m in ("aiohttp", "aiosqlite", "fitz")),
          "pip install -r requirements.txt", critical=True)
    milvus_ok = module_available("pymilvus")
    if platform.system() == "Windows":
        check(
            "Milvus Lite（Windows 无原生支持）",
            False,
            "三选一：外部 Milvus 服务 URI / WSL 内运行 / 回退 SQLite 词法检索（自动降级）",
        )
    else:
        check(
            "pymilvus (Milvus Lite 向量检索)",
            milvus_ok,
            "uv sync 或 pip install pymilvus[milvus_lite]",
        )
    check("sentence-transformers (本地 Embedding)", module_available("sentence_transformers"),
          "extras: local-embedding；未装则向量/图谱检索降级")
    check("lightrag (知识图谱)", module_available("lightrag"), "extras: graph")
    check("boto3 (R2 备份)", module_available("boto3"), "extras: backup")
    check("mcp (Notion stdio 传输)", module_available("mcp"), "extras: notion")
    check("npx (Notion MCP server 运行时)", shutil.which("npx") is not None,
          "启用 Notion 同步需安装 Node.js")

    print("—— 体检" + ("通过" if failures == 0 else f"失败（{failures} 项关键问题）"))
    return 0 if failures == 0 else 1


def _run_passwd(args: argparse.Namespace) -> int:
    from standalone.config_bootstrap import _write_config  # 同包内部复用原子写

    data_dir = resolve_data_dir(args.data_dir)
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}（先运行一次 serve 生成）", file=sys.stderr)
        return 1
    new_password = args.new_password
    if not new_password:
        new_password = getpass.getpass("新密码：")
        confirm = getpass.getpass("再次输入：")
        if new_password != confirm:
            print("两次输入不一致，未修改。", file=sys.stderr)
            return 1
    if not new_password:
        print("密码不能为空，未修改。", file=sys.stderr)
        return 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("web_console", {})["password"] = new_password
    _write_config(config_path, config)
    print("登录密码已更新（重启 serve 后生效；env KR_WEB_PASSWORD 存在时仍以其为准）。")
    return 0


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
