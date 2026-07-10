"""standalone_config.json 引导：首启生成默认模板 + 随机登录密码，读取时补齐缺失密码。

该文件是独立壳的「原始配置 dict」来源（AstrBot 面板注入的等价物），后续仍会经
``RuntimeConfigStore.merged_with(raw)`` 与 WebUI 持久化的 runtime_config.json 合并，
两壳共用同一条配置合并链。机密始终可由环境变量覆盖（KR_WEB_PASSWORD 等，见
``ka_core/config.py``）。文件以 0600 权限写入。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger("ka_standalone")

CONFIG_FILENAME = "standalone_config.json"

# 默认模板：只放独立壳需要显式呈现/常改的节；未列出的键走 ka_core/config.py 默认值。
DEFAULT_CONFIG: dict[str, Any] = {
    "web_console": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 26618,
        "username": "admin",
        "password": "",
    },
    "main_llm": {
        "base_url": "",
        "model": "",
        "api_key": "",
        "timeout_seconds": 300,
        "max_retries": 2,
    },
    "embedding": {"provider": "local"},
    "r2_sync": {"enabled": False},
    "notion_sync": {"enabled": False},
}


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _chmod_private(tmp)
    tmp.replace(path)
    _chmod_private(path)


def load_or_create(data_dir: Path) -> tuple[dict[str, Any], str | None]:
    """读取（或首启创建）standalone_config.json。

    返回 ``(raw_config, generated_password)``：仅当本次调用生成了新登录密码时第二项
    非 None（调用方负责把它展示给用户一次）。密码解析规则：环境变量 KR_WEB_PASSWORD
    优先于文件字段（由 ka_core Config 统一处理）；两者皆空时生成随机密码并持久化，
    保证 serve 总是可登录。
    """
    path = data_dir / CONFIG_FILENAME
    generated: str | None = None

    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法解析 {path}：{exc}") from exc
        if not isinstance(config, dict):
            raise RuntimeError(f"{path} 顶层必须是 JSON object")
    else:
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝模板

    web = config.setdefault("web_console", {})
    file_password = str(web.get("password") or "")
    env_password = os.environ.get("KR_WEB_PASSWORD", "")
    if not file_password and not env_password:
        generated = secrets.token_urlsafe(9)
        web["password"] = generated

    if generated is not None or not path.exists():
        _write_config(path, config)
        logger.info("standalone 配置已写入 %s", path)

    return config, generated


__all__ = ["CONFIG_FILENAME", "DEFAULT_CONFIG", "load_or_create"]
