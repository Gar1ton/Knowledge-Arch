"""stdio MCP Notion 传输：独立壳的 ``ToolCaller`` 实现（AstrBot MCP 管理器的等价物）。

经 ``npx -y @suekou/mcp-notion-server@1.2.x`` 拉起同一款 MCP server（工具契约与插件
模式逐字对齐，见 ka_core/adapters/notion_mcp.py 的版本锁定说明），把
``NotionMCPAdapter`` 的注入协议 ``async (tool_name, arguments) -> CallToolResult``
落到 mcp SDK 的 stdio 会话上。返回值原样透传——适配器的 ``_unwrap`` 已能解包
mcp ``CallToolResult``（.content/.isError）。

生命周期：会话惰性建立（首次调用才 spawn 子进程）；调用失败自动重建会话重试一次
（对齐宿主 ``call_tool_with_reconnect`` 语义）；``aclose()`` 由组合根 teardown 调用。
API token 经环境变量 ``KR_NOTION_API_TOKEN`` 注入子进程的 ``NOTION_API_TOKEN``。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import AsyncExitStack
from typing import Any

logger = logging.getLogger("ka_standalone")

ENV_NOTION_TOKEN = "KR_NOTION_API_TOKEN"
# 锁 1.2.x：v2 转向 Data Source API，参数/响应破坏性变更会使适配层整体失效。
DEFAULT_SERVER_PACKAGE = "@suekou/mcp-notion-server@1.2.x"


class StdioNotionToolCaller:
    """实现 ``NotionMCPAdapter`` 的 ToolCaller 注入协议（可 await 调用对象）。"""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        npx_command: str = "npx",
        server_package: str = DEFAULT_SERVER_PACKAGE,
        request_timeout_sec: int = 60,
        session_factory: Any | None = None,
    ) -> None:
        self._api_token = (
            api_token if api_token is not None else os.environ.get(ENV_NOTION_TOKEN, "")
        )
        self._npx_command = npx_command
        self._server_package = server_package
        self._request_timeout_sec = max(1, int(request_timeout_sec))
        # 测试注入口：async () -> (session, aclose_coro_fn)；生产为 None 走 mcp SDK。
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any | None = None

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    session = await self._ensure_session()
                    return await asyncio.wait_for(
                        session.call_tool(tool_name, arguments),
                        timeout=self._request_timeout_sec,
                    )
                except Exception as exc:  # noqa: BLE001 - 统一重连语义，最终显式抛出
                    last_exc = exc
                    await self._close_session()
                    if attempt == 1:
                        logger.warning(
                            "Notion MCP 调用失败（%s），重建会话后重试：%s", tool_name, exc
                        )
            raise RuntimeError(
                f"Notion MCP 工具 {tool_name} 经重连重试后仍失败：{last_exc}"
            ) from last_exc

    async def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        if self._session_factory is not None:
            self._session = await self._session_factory()
            return self._session
        if not self._api_token:
            raise RuntimeError(
                f"Notion API token 未设置：请配置环境变量 {ENV_NOTION_TOKEN}"
                "（Notion internal integration secret）。"
            )
        if shutil.which(self._npx_command) is None:
            raise RuntimeError(
                f"未找到 {self._npx_command}：独立版 Notion 同步需要 Node.js/npx "
                "以拉起 @suekou/mcp-notion-server（见 README 部署说明）。"
            )
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._npx_command,
            args=["-y", self._server_package],
            env={**os.environ, "NOTION_API_TOKEN": self._api_token},
        )
        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=self._request_timeout_sec)
        except Exception:
            await self._close_session()
            raise
        self._session = session
        logger.info("Notion MCP stdio 会话已建立（%s）", self._server_package)
        return session

    async def _close_session(self) -> None:
        self._session = None
        if self._exit_stack is not None:
            stack, self._exit_stack = self._exit_stack, None
            try:
                await stack.aclose()
            except Exception as exc:  # noqa: BLE001 - 关闭失败只记日志
                logger.debug("Notion MCP 会话关闭异常：%s", exc)

    async def aclose(self) -> None:
        """显式释放子进程与会话（组合根 teardown 调用）。"""
        async with self._lock:
            await self._close_session()


__all__ = ["ENV_NOTION_TOKEN", "StdioNotionToolCaller"]
