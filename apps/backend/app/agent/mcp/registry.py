"""MCP 服务器注册表：谁配了、怎么起、工具怎么转、调用怎么分发。

**进程什么时候起**：懒启动。第一次需要工具清单时才拉起子进程 ——
不让"没人用 MCP"的项目白起一个进程。

**进程什么时候关**：应用退出时（main.py 的 lifespan 里）。
不关的话，uvicorn 退出后子进程会变成孤儿留在系统里。
"""

from __future__ import annotations

import sys
from typing import Any

from .client import MCPClient, MCPError
from .convert import parse_name, to_openai_schema

__all__ = ["MCPServers", "mcp_servers"]

# 配置：服务器名 → 启动命令。
#
# 这里写死是为了教学清晰 —— 真实的 MCP 客户端会从配置文件
# （claude_desktop_config.json 那种）读，形状差不多是一个 dict。
#
# 用 sys.executable 而不是 "python3"：必须用当前这个解释器，
# 否则装不上依赖的环境里会起不来（和测试里踩的是同一个坑）。
MCPServers = dict[str, list[str]]


def default_servers() -> MCPServers:
    return {
        "weather": [sys.executable, "-m", "app.agent.mcp.weather_server"],
    }


class MCPRegistry:
    """管理所有 MCP 连接。

    对外只暴露三件事：拿工具清单、执行工具、关掉全部。
    """

    def __init__(self, servers: MCPServers) -> None:
        self.servers = servers
        self._clients: dict[str, MCPClient] = {}
        # 缓存工具清单：它是个网络/进程往返，不该每次请求模型都做一遍
        self._tools: list[dict[str, Any]] | None = None
        # 记下工具名 → 服务器，执行时要用
        self._owner: dict[str, str] = {}

    def _client(self, server: str) -> MCPClient:
        client = self._clients.get(server)
        if client is None:
            cmd = self.servers.get(server)
            if cmd is None:
                raise MCPError(f"没有配置名为 {server!r} 的 MCP 服务器")
            client = MCPClient(cmd)
            self._clients[server] = client
        return client

    async def schemas(self) -> list[dict[str, Any]]:
        """所有 MCP 工具，已经转成 OpenAI 的形状。

        **单个服务器挂了不能拖垮全部**：一个配置错的服务，
        不该让整个对话发不出去。所以这里逐个 try，失败的跳过。
        """
        if self._tools is not None:
            return self._tools

        merged: list[dict[str, Any]] = []
        owner: dict[str, str] = {}

        for server in self.servers:
            try:
                client = self._client(server)
                await client.start()
                raw_tools = await client.list_tools()
            except (MCPError, OSError, TimeoutError) as exc:
                print(f"[mcp] 服务器 {server!r} 不可用，跳过：{exc}")
                continue

            for raw in raw_tools:
                schema = to_openai_schema(server, raw)
                if schema is None:
                    continue
                name = schema["function"]["name"]
                if name in owner:
                    print(f"[mcp] 工具名重复，跳过：{name}")
                    continue
                merged.append(schema)
                owner[name] = server

        self._tools = merged
        self._owner = owner
        return merged

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """执行一个 MCP 工具。名字必须是 `mcp__server__tool` 的形式。"""
        parsed = parse_name(name)
        if parsed is None:
            return f"错误：{name!r} 不是 MCP 工具名（应形如 mcp__server__tool）"

        server, tool = parsed
        try:
            client = self._client(server)
            await client.start()
        except MCPError as exc:
            # 不抛异常：把错误当成"工具结果"交回模型，
            # 它看到错误往往能自己换个办法（S3 就定下的原则）
            return f"错误：{exc}"

        try:
            return await client.call_tool(tool, arguments)
        except MCPError as exc:
            return f"工具执行出错：{exc}"

    async def aclose(self) -> None:
        """关掉所有连接。应用退出时调用。"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        self._tools = None
        self._owner.clear()


# 全局单例：和 limiter 一样，必须跨请求共享 ——
# 每个请求起一套 MCP 进程就失去意义了
mcp_servers = MCPRegistry(default_servers())
