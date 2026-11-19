"""后端进程生命周期原语（shell 层工具，无业务）。

- DataDirLock：同一数据目录只允许一个后端进程。两个进程同时打开 SQLite/Milvus Lite
  会互相持锁（Milvus 的 DataDirLockedError 就是这类问题），所以第二个进程必须在装配前退出。
- wait_for_shutdown：等到「收到退出请求 / 收到 SIGTERM·SIGINT / 父进程消失」之一。
  父进程（桌面壳）崩溃后若后端继续活着，会一直持有 Milvus 锁，下次启动必然失败。
- announce_ready：向 stdout 打印 `KA_READY port=…`，桌面壳据此拿到系统分配的端口。
- keep_listening：监听 socket 被事件循环关掉后在原端口重新监听。Windows 的 Proactor 循环在
  accept 出错（如 WinError 64：客户端在握手中途断开）时会 `sock.close()` 且不再恢复，
  进程活着却不再接受连接。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import IO, Any

logger = logging.getLogger("ka_server")

READY_PREFIX = "KA_READY"
# 父进程存活轮询间隔：只是兜底信号，1 秒足够及时又几乎不耗资源。
_PARENT_POLL_SECONDS = 1.0
_LISTENER_POLL_SECONDS = 1.0


class DataDirLockedError(RuntimeError):
    """数据目录已被另一个后端进程持有。"""


class DataDirLock:
    """数据目录上的独占文件锁（进程退出时由操作系统自动释放，崩溃也不会残留）。

    锁文件本身不删除：锁是 OS 级的，文件是否存在不代表是否被持有。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+", encoding="utf-8")  # noqa: SIM115 - 需跨方法持有
        try:
            _lock_file(handle)
        except OSError as exc:
            handle.close()
            raise DataDirLockedError(
                f"{self._path.parent}: another Knowledge Arch backend holds the data directory"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            _unlock_file(self._handle)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> DataDirLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock_file(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pid_alive(pid: int) -> bool:
    """判断进程是否仍在运行。

    Windows 上不能用 os.kill(pid, 0)：信号 0 在那里是 CTRL_C_EVENT，会真的发出去。
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 0) == wait_timeout)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGTERM / SIGINT 触发有序退出；不支持 add_signal_handler 的平台退回 signal.signal。"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def wait_for_shutdown(stop: asyncio.Event, parent_pid: int | None = None) -> str:
    """阻塞到需要退出为止，返回原因（requested / parent_exited）。"""
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=_PARENT_POLL_SECONDS)
            return "requested"
        except TimeoutError:
            if parent_pid is not None and not pid_alive(parent_pid):
                logger.warning("Parent process %s exited; shutting down the backend.", parent_pid)
                return "parent_exited"


def bound_port(site: Any) -> int:
    """取 TCPSite 实际绑定的端口（--port 0 时由系统分配）。"""
    server = site._server  # aiohttp 未公开该属性的稳定接口；仅读取 sockets
    if server is None or not server.sockets:
        raise RuntimeError("HTTP site is not listening")
    return int(server.sockets[0].getsockname()[1])


def announce_ready(port: int, stream: IO[str] | None = None) -> None:
    """打印就绪行；只含端口，绝不打印 token。"""
    out = stream or sys.stdout
    out.write(f"{READY_PREFIX} port={port}\n")
    out.flush()


__all__ = [
    "READY_PREFIX",
    "DataDirLock",
    "DataDirLockedError",
    "announce_ready",
    "bound_port",
    "install_signal_handlers",
    "pid_alive",
    "wait_for_shutdown",
]


def listener_closed(site: Any) -> bool:
    """TCPSite 的监听 socket 是否都已关闭（关闭后 fileno 为 -1；server 关闭后 sockets 为空）。"""
    server = site._server  # 同 bound_port：只读 sockets
    return server is None or not server.sockets or all(s.fileno() == -1 for s in server.sockets)


async def keep_listening(
    site: Any, relisten: Callable[[], Awaitable[Any]], stop: asyncio.Event
) -> None:
    """每秒检查监听 socket；被关掉就调 relisten() 在原端口建新的 site，失败下一轮再试。

    正常退出时先取消本任务再关 HTTP（serve 的 finally），不会把主动关闭误当成故障。
    """
    while not stop.is_set():
        await asyncio.sleep(_LISTENER_POLL_SECONDS)
        if stop.is_set() or not listener_closed(site):
            continue
        logger.warning("HTTP listener was closed by the event loop; listening again on port")
        try:
            site = await relisten()
        except OSError as exc:
            logger.error("Listening again failed, will retry: %s", exc)
            continue
        logger.info("HTTP listener restored.")
