"""MCP 客户端：拉起一个子进程，通过 stdin/stdout 说 JSON-RPC。

生命周期就三件事，和所有"长驻子进程"的代码一样：

    启动 → 握手 → 用 → 关闭时杀掉

**最容易被忽略的是最后一步**。子进程不会因为父进程的对象被回收就死掉，
它会变成孤儿继续跑。所以 close() 里必须显式 kill，
而且必须放在 finally / __aexit__ 里 —— 否则异常路径上就漏了。
"""

from __future__ import annotations

import asyncio
import itertools
import os
from typing import Any

from .protocol import PROTOCOL_VERSION, decode, encode, is_response_to, rpc_error

__all__ = ["MCPClient", "MCPError"]


class MCPError(Exception):
    """MCP 交互失败（进程起不来、超时、服务器报错）。"""


class MCPClient:
    """一个 MCP 服务器连接。

    用法：
        async with MCPClient(["python3", "server.py"]) as c:
            tools = await c.list_tools()
            text = await c.call_tool("get_weather", {"city": "北京"})

    **并发限制**：stdout 只有一条，读的时候必须先知道"我要哪条响应"。
    多个请求同时飞进来，就得有个多路复用器按 id 分发（要一个读协程 + 一个
    id→Future 的表）。这里不做 —— agent 一次只调一个工具，串行足够。
    用锁把请求排成队，简单且不会错。
    """

    def __init__(
        self,
        command: list[str],
        *,
        timeout: float = 20.0,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.timeout = timeout
        self.cwd = cwd

        self._proc: asyncio.subprocess.Process | None = None
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()   # 让请求排队，避免抢同一条 stdout
        self.server_info: dict[str, Any] = {}

    # ── 生命周期 ────────────────────────────────────────

    async def start(self) -> None:
        """拉起进程并完成握手。失败时不留半死不活的进程。"""
        if self._proc is not None:
            return

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.cwd or os.getcwd(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # stderr 不接管道：让它直接打到终端。
                # 接管道又不读，缓冲区满了服务器会卡死 —— 这是经典死锁。
                stderr=None,
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"MCP 服务器启动失败：{exc}") from exc

        try:
            result = await self._request(
                "initialize",
                {
                    # 服务器回它自己支持的版本。我们不做版本校验 ——
                    # 这个项目只用 tools/list 和 tools/call，两者在
                    # 各版本里形状没变过。真需要严格校验时再加。
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mini-agent", "version": "0.1.0"},
                },
            )
            self.server_info = result.get("serverInfo") or {}

            # 握手最后一步：告诉服务器"我准备好了"。
            # 这是**通知**，没有 id，服务器不会回。
            await self._notify("notifications/initialized")
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """关掉子进程。必须能被重复调用（finally 里可能进来两次）。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()

        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                pass  # 已经 kill 了还等不到，没法做得更多

    async def __aenter__(self) -> "MCPClient":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── 协议方法 ────────────────────────────────────────

    async def list_tools(self) -> list[dict[str, Any]]:
        """服务器有哪些工具（MCP 自己的 schema，不是 OpenAI 的）。"""
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用工具，返回纯文本。

        MCP 的返回是 content 数组（可能混合文本、图片、资源）。
        这里只取文本 —— 模型能看懂的就这些，图片要走另一条路。
        """
        result = await self._request("tools/call", {"name": name, "arguments": arguments})

        if result.get("isError"):
            # 工具自己报告失败。这**不是**协议错误，
            # 是"调用成功但业务失败"，比如查不到城市。
            return f"工具返回错误：{_text_of(result)}"

        return _text_of(result)

    # ── 内部：发请求 / 收响应 ───────────────────────────

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        await self._write(payload)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发一个请求，等到 id 对得上的那条响应。"""
        async with self._lock:
            req_id = next(self._ids)
            await self._write(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            )
            return await asyncio.wait_for(
                self._read_response(req_id), timeout=self.timeout
            )

    async def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError("MCP 服务器没有启动")
        try:
            proc.stdin.write(encode(payload))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            # 服务器已经死了。这种错误要翻译成人能懂的话，
            # 否则一路往上冒，最后变成一句 BrokenPipeError 给用户看。
            raise MCPError("MCP 服务器已退出（写入失败）") from exc

    async def _read_response(self, request_id: int) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise MCPError("MCP 服务器没有启动")

        while True:
            line = await proc.stdout.readline()
            if not line:
                # 空 = 对端关闭了 stdout = 进程没了
                raise MCPError("MCP 服务器已退出")

            msg = decode(line)
            if msg is None:
                continue      # 非 JSON 的噪音（服务器日志之类），跳过

            if msg.get("id") != request_id:
                # 不是我要的：可能是别的请求的响应，也可能是服务器发的通知。
                # 这里直接丢掉 —— 串行模型下不会有别的在飞。
                continue

            if "error" in msg:
                raise MCPError(rpc_error(msg))
            return msg.get("result") or {}


def _text_of(result: dict[str, Any]) -> str:
    """把 MCP 的 content 数组拼成纯文本。"""
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            # 图片 / 资源：现在不处理，但要留个痕迹，
            # 不然用户会以为工具什么都没返回
            parts.append(f"[{item.get('type')} 内容，暂不支持显示]")
    return "\n".join(parts).strip() or "（工具没有返回内容）"
