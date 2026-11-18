"""MCP 客户端（S10-1）。

测试对象是我们自己的客户端 + 自己的天气服务器。
用自建服务器而不是第三方，是为了测试**不依赖网络、不依赖别人**，
而且协议的两端都看得见。
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.agent.mcp import MCPClient, MCPError
from app.agent.mcp.protocol import decode, encode, is_response_to


def _server(*args: str) -> list[str]:
    """用当前解释器跑我们自己的服务器。

    必须用 sys.executable 而不是写死 python3 ——
    测试跑在 uv 的虚拟环境里，系统的 python3 没有我们的包。
    """
    return [sys.executable, "-m", "app.agent.mcp.weather_server", *args]


@pytest.fixture
async def client():
    async with MCPClient(_server(), timeout=10) as c:
        yield c


# ── 报文格式（协议层）────────────────────────────────────


def test_encode_is_one_line() -> None:
    """一条报文必须是一行 —— 换行就是分帧符。"""
    raw = encode({"jsonrpc": "2.0", "id": 1})
    assert raw.count(b"\n") == 1
    assert raw.endswith(b"\n")


def test_encode_keeps_chinese_readable() -> None:
    """中文不转成 \\uXXXX。

    转义不会错，但调试时看日志全是 \\u5317\\u4eac，根本没法排查。
    """
    raw = encode({"city": "北京"})
    assert "北京".encode() in raw


def test_decode_tolerates_garbage() -> None:
    """混进来的日志行不能让客户端崩。

    很多 MCP 服务器会往 stdout 打日志（规范说该打 stderr，但它们不听话）。
    """
    assert decode(b"not json at all\n") is None
    assert decode(b"\n") is None
    assert decode(b'{"id":1}\n') == {"id": 1}


def test_is_response_to_matches_only_its_id() -> None:
    """响应必须按 id 认。

    一个连接上可能同时有多个请求，响应顺序不保证 ——
    不按 id 匹配就会把 A 的结果当成 B 的。
    """
    assert is_response_to({"id": 2, "result": {}}, 2)
    assert not is_response_to({"id": 3, "result": {}}, 2)
    # 通知没有 id，不是任何请求的响应
    assert not is_response_to({"method": "notifications/progress"}, 2)


# ── 握手 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handshake_records_server_info(client: MCPClient) -> None:
    assert client.server_info.get("name") == "weather"


@pytest.mark.asyncio
async def test_start_twice_is_idempotent() -> None:
    """重复 start 不能起第二个进程。"""
    async with MCPClient(_server(), timeout=10) as c:
        proc = c._proc
        await c.start()
        assert c._proc is proc


# ── tools/list ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tools_returns_weather(client: MCPClient) -> None:
    tools = await client.list_tools()
    names = [t["name"] for t in tools]
    assert "get_weather" in names

    tool = next(t for t in tools if t["name"] == "get_weather")
    # MCP 用的是 inputSchema，不是 OpenAI 的 parameters ——
    # 这两个形状不一样，S10-2 要做转换
    assert "inputSchema" in tool
    assert "city" in tool["inputSchema"]["properties"]


# ── tools/call ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_returns_text(client: MCPClient) -> None:
    text = await client.call_tool("get_weather", {"city": "北京"})
    assert "北京" in text
    assert "晴" in text


@pytest.mark.asyncio
async def test_business_error_is_not_a_crash(client: MCPClient) -> None:
    """工具层面的失败（查不到城市）不能被当成协议错误。

    协议错误 = 服务坏了，该中断；
    业务错误 = 参数不对，应该原样交给模型，让它自己改。
    """
    text = await client.call_tool("get_weather", {"city": "火星"})
    assert "火星" in text
    assert "工具返回错误" in text


@pytest.mark.asyncio
async def test_unknown_tool_raises(client: MCPClient) -> None:
    """调用不存在的工具是协议错误（服务器回 error）。"""
    with pytest.raises(MCPError, match="没有这个工具"):
        await client.call_tool("no_such_tool", {})


@pytest.mark.asyncio
async def test_sequential_requests_get_their_own_answers(client: MCPClient) -> None:
    """连续发多次，每次都要拿到自己那条 —— 验证 id 匹配没搞错。

    如果不按 id 匹配，第二次就会读到第一次残留的响应。
    """
    a = await client.call_tool("get_weather", {"city": "北京"})
    b = await client.call_tool("get_weather", {"city": "上海"})
    c = await client.call_tool("get_weather", {"city": "广州"})

    assert "北京" in a and "上海" not in a
    assert "上海" in b and "北京" not in b
    assert "广州" in c


# ── 进程生命周期（最容易漏的部分）──────────────────────


@pytest.mark.asyncio
async def test_close_kills_the_process() -> None:
    """关闭后进程必须真的消失。

    子进程不会因为对象被回收就死掉 —— 它会变成孤儿继续跑。
    """
    c = MCPClient(_server(), timeout=10)
    await c.start()
    proc = c._proc
    assert proc is not None
    assert proc.returncode is None        # 还活着

    await c.close()

    assert proc.returncode is not None, "close 之后进程还活着 —— 孤儿进程"


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    """重复 close 不能抛异常（finally 里可能进来两次）。"""
    c = MCPClient(_server(), timeout=10)
    await c.start()
    await c.close()
    await c.close()
    await c.close()


@pytest.mark.asyncio
async def test_context_manager_closes_on_exception() -> None:
    """出异常时也要关进程 —— 这是 __aexit__ 存在的意义。"""
    holder: dict[str, object] = {}

    with pytest.raises(RuntimeError, match="业务炸了"):
        async with MCPClient(_server(), timeout=10) as c:
            holder["proc"] = c._proc
            raise RuntimeError("业务炸了")

    proc = holder["proc"]
    assert proc.returncode is not None, "异常路径上没关进程"


@pytest.mark.asyncio
async def test_broken_server_raises_readable_error() -> None:
    """服务器起不来时，报错要能看懂，不能是 OSError 原文。"""
    c = MCPClient(["/nonexistent/binary/xyz"], timeout=5)
    with pytest.raises(MCPError, match="启动失败"):
        await c.start()


@pytest.mark.asyncio
async def test_server_dying_midway_is_detected() -> None:
    """服务器中途死了，要报"已退出"而不是卡住或抛底层错误。"""
    c = MCPClient(_server(), timeout=5)
    await c.start()

    proc = c._proc
    assert proc is not None
    proc.kill()                     # 模拟服务器崩溃
    await proc.wait()

    with pytest.raises(MCPError):
        await c.list_tools()

    await c.close()


@pytest.mark.asyncio
async def test_request_timeout_is_enforced() -> None:
    """服务器不回话时，不能永远等。

    用 `sleep 30` 当服务器：它读了 stdin 但一个字都不吐，
    客户端的 readline 会一直挂着 → 必须有超时兜底。

    一开始我用的是 /bin/cat，结果它把请求原样吐回来了，id 正好对得上，
    客户端当成响应收下了 —— 那不是"不回话"，而是一个很巧的假服务器。
    """
    c = MCPClient(["sleep", "30"], timeout=0.5)
    with pytest.raises((MCPError, asyncio.TimeoutError)):
        await c.start()
    await c.close()
