"""MCP（Model Context Protocol）接入。

只做 stdio 传输这一种 —— 本地子进程，用 stdin/stdout 说 JSON-RPC。
远程 HTTP 传输用不上，也更容易出错。

**为什么手写而不是用官方 mcp SDK**：
这跟"不用 LangChain、不用 OpenAI SDK"是同一个理由。
SDK 把 JSON-RPC 分帧、进程生命周期、错误处理全藏起来，
而这几个恰恰是唯一值得学的部分。手写一百多行，全都看得见。
"""

from __future__ import annotations

from .client import MCPClient, MCPError

__all__ = ["MCPClient", "MCPError"]
