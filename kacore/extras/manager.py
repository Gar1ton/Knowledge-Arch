"""可选组件管理：安装到 `<data_dir>/extras/<pack>/`、启动时激活、状态查询、卸载。

组件清单的唯一真相源是 `kacore/capabilities.OPTIONAL_DEPENDENCIES`（pack key = 依赖 key）。

安装是事务性的：先装进 `extras/.<pack>.partial/`，安装器成功后写入标记文件，再把旧目录挪开、
新目录换入；任何一步失败都删除临时目录、旧版本原样保留。标记文件记录 Python 主次版本——
应用升级换了解释器版本后，旧组件不再激活，界面提示重新安装。

同名包冲突的规则：组件目录追加在 sys.path 末尾，运行时自带的包永远优先；组件目录里与运行时
重复的包只是不会被加载的副本。不用版本约束文件把运行时的包钉死——那会让依赖范围稍有出入的
组件（如 botocore 对 python-dateutil 的上限）直接装不上。
"""

from __future__ import annotations

import importlib
import json
import logging
import shutil
import site
import sys
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kacore.capabilities import OPTIONAL_DEPENDENCIES, OptionalDependency, resolve_dependency
from kacore.extras.base import ExtrasInstaller, ExtrasUnavailableError, LineSink
from kacore.extras.failure import classify_install_failure

logger = logging.getLogger("ExtrasManager")

MARKER_FILE = ".ka_extras.json"
_OUTPUT_TAIL_LINES = 400  # 失败归类只看输出末尾；torch 的下载日志很长

STATE_INSTALLED = "installed"
STATE_MISSING = "missing"
STATE_STALE = "stale"  # 为另一个 Python 版本安装，需要重装


def python_tag() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


class ExtrasManager:
    """组件目录的唯一读写入口（组合根 / 进程薄壳构造一次）。"""

    def __init__(
        self,
        root: Path,
        installer: ExtrasInstaller,
        *,
        python: str | None = None,
        add_site_dir: Callable[[str], None] = site.addsitedir,
    ) -> None:
        self._root = Path(root)
        self._installer = installer
        self._python_tag = python or python_tag()
        self._add_site_dir = add_site_dir
        self._activated: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def installer(self) -> ExtrasInstaller:
        return self._installer

    def pack_dir(self, key: str) -> Path:
        return self._root / key

    # ── 状态 ──────────────────────────────────────────────────────

    def _marker(self, key: str) -> dict[str, Any] | None:
        path = self.pack_dir(key) / MARKER_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def state(self, key: str) -> str:
        marker = self._marker(key)
        if marker is None:
            return STATE_MISSING
        if marker.get("python") != self._python_tag:
            return STATE_STALE
        return STATE_INSTALLED

    def status(self) -> dict[str, Any]:
        """组件页数据：安装器是否可用 + 每个组件的状态（不含核心依赖）。"""
        from kacore.capabilities import module_available

        components = []
        for dep in OPTIONAL_DEPENDENCIES:
            if not dep.component:
                continue
            state = self.state(dep.key)
            marker = self._marker(dep.key) or {}
            components.append(
                {
                    "key": dep.key,
                    "feature": dep.feature,
                    "specs": list(dep.install_specs),
                    "state": state,
                    "importable": module_available(dep.import_name),
                    "active": dep.key in self._activated,
                    "installed_at": marker.get("installed_at"),
                    "size_bytes": (
                        _dir_size(self.pack_dir(dep.key)) if state != STATE_MISSING else 0
                    ),
                }
            )
        return {
            "installer_available": self._installer.available,
            "installer": self._installer.describe(),
            "extras_dir": str(self._root),
            "components": components,
        }

    # ── 激活 ──────────────────────────────────────────────────────

    def activate(self) -> list[str]:
        """把已装组件目录加入 sys.path（追加在运行时之后，运行时自带的包优先）。

        幂等；返回本次新激活的 pack key。为另一个 Python 版本装的组件跳过并记警告。
        """
        activated = []
        for dep in OPTIONAL_DEPENDENCIES:
            if dep.key in self._activated:
                continue
            state = self.state(dep.key)
            if state == STATE_STALE:
                logger.warning(
                    "Component %s was installed for another Python version and was skipped; "
                    "reinstall it on the Components page", dep.key
                )
                continue
            if state != STATE_INSTALLED:
                continue
            path = str(self.pack_dir(dep.key))
            if path not in sys.path:
                self._add_site_dir(path)
            self._activated.add(dep.key)
            activated.append(dep.key)
        if activated:
            importlib.invalidate_caches()
            logger.info("activated components: %s", ", ".join(activated))
        return activated

    # ── 安装 / 卸载 ──────────────────────────────────────────────

    async def install(self, package: str, *, on_line: LineSink | None = None) -> dict[str, Any]:
        """安装一个组件（package 为依赖 key 或主规格，须在白名单内，否则抛 ValueError）。

        返回 {status, package, returncode, restart_required, message}；失败时另有
        reason（见 kacore.extras.failure）与 detail（最相关的几行原文）。失败不改动已装版本。
        """
        dep = resolve_dependency(package)
        self._root.mkdir(parents=True, exist_ok=True)
        staging = self._root / f".{dep.key}.partial"
        _remove(staging)
        staging.mkdir(parents=True)
        tail: deque[str] = deque(maxlen=_OUTPUT_TAIL_LINES)

        def record(line: str) -> None:
            tail.append(line)
            if on_line is not None:
                on_line(line)

        try:
            returncode = await self._installer.install(
                dep.install_specs,
                staging,
                extra_args=dep.installer_args,
                on_line=record,
            )
        except ExtrasUnavailableError as exc:
            _remove(staging)
            return self._result(dep, "error", None, str(exc))
        except Exception as exc:  # noqa: BLE001 - 子进程启动失败等，统一回滚并报告
            _remove(staging)
            logger.error("component %s install raised: %s", dep.key, exc, exc_info=True)
            return self._result(dep, "error", None, f"Installation failed: {exc}")
        if returncode != 0:
            _remove(staging)
            failure = classify_install_failure(list(tail), returncode)
            logger.error("component %s install failed (%s)", dep.key, failure.reason)
            return {
                **self._result(dep, "error", returncode, failure.message),
                "reason": failure.reason,
                "detail": list(failure.detail),
            }
        (staging / MARKER_FILE).write_text(
            json.dumps(
                {
                    "key": dep.key,
                    "specs": list(dep.install_specs),
                    "python": self._python_tag,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            self._swap_in(dep.key, staging)
        except OSError as exc:
            _remove(staging)
            logger.error("component %s swap-in failed: %s", dep.key, exc)
            return self._result(
                dep,
                "error",
                returncode,
                f"The component folder is in use, so the old version could not be replaced: {exc}. "
                "Restart the service and try again.",
            )
        self.activate()
        logger.info("component %s installed", dep.key)
        return self._result(dep, "ok", returncode, "Installed; restart the service to use it.")

    def _swap_in(self, key: str, staging: Path) -> None:
        final = self.pack_dir(key)
        backup = self._root / f".{key}.old"
        _remove(backup)
        moved_old = False
        if final.exists():
            final.rename(backup)
            moved_old = True
        try:
            staging.rename(final)
        except OSError:
            if moved_old:
                backup.rename(final)
            raise
        _remove(backup)

    def uninstall(self, package: str) -> dict[str, Any]:
        """删除组件目录（重启服务后生效；已加载的模块在本进程内仍可用）。"""
        dep = resolve_dependency(package)
        target = self.pack_dir(dep.key)
        if not target.exists():
            return self._result(dep, "ok", None, "The component is not installed.", restart=False)
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return self._result(dep, "error", None, (
                f"Cannot delete the component folder: {exc}. Restart the service and try again."
            ))
        return self._result(dep, "ok", None, "Uninstalled; restart the service to apply.")

    @staticmethod
    def _result(
        dep: OptionalDependency,
        status: str,
        returncode: int | None,
        message: str,
        *,
        restart: bool | None = None,
    ) -> dict[str, Any]:
        ok = status == "ok"
        return {
            "status": status,
            "package": " ".join(dep.install_specs),
            "key": dep.key,
            "returncode": returncode,
            "restart_required": ok if restart is None else restart,
            "message": message,
        }


__all__ = [
    "MARKER_FILE",
    "STATE_INSTALLED",
    "STATE_MISSING",
    "STATE_STALE",
    "ExtrasManager",
    "python_tag",
]
