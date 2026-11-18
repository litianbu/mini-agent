"""S10-2 + S10-3 的集成：模型能不能看到并调用 MCP 工具。

这里测的是"接线"，不是协议细节（那是 test_s10_mcp_client.py 的事）。
"""

from __future__ import annotations

import sys

import pytest

from app.agent.mcp.registry import MCPRegistry
from app.agent.tools import ALL_TOOLS, execute, schemas


def _weather_cmd() -> list[str]:
    return [sys.executable, "-m", "app.agent.mcp.weather_server"]


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch):
    """每个用例一个干净的注册表，避免工具清单缓存串味。"""
    from app.agent.mcp import registry as reg_mod

    reg = MCPRegistry({"weather": _weather_cmd()})
    monkeypatch.setattr(reg_mod, "mcp_servers", reg)
    # tools/__init__.py 里 import 的是模块级名字，要一起换掉
    import app.agent.tools as tools_mod

    monkeypatch.setattr(tools_mod, "mcp_servers", reg)
    yield reg


# ── 模型看到的清单 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_model_sees_both_local_and_mcp_tools(fresh_registry) -> None:
    """本地 6 个 + MCP 1 个，一个都不能少。"""
    tools = await schemas()
    names = [t["function"]["name"] for t in tools]

    for local in ALL_TOOLS:
        assert local.name in names, f"本地工具 {local.name} 丢了"
    assert "mcp__weather__get_weather" in names


@pytest.mark.asyncio
async def test_local_tool_order_is_stable(fresh_registry) -> None:
    """本地工具必须排在最前面 —— 顺序稳定，日志和测试才好读。"""
    tools = await schemas()
    names = [t["function"]["name"] for t in tools]
    assert names[: len(ALL_TOOLS)] == [t.name for t in ALL_TOOLS]


@pytest.mark.asyncio
async def test_broken_server_does_not_break_local_tools(fresh_registry, monkeypatch) -> None:
    """一个 MCP 服务器配置错了，本地工具必须照常可用。

    这是最容易出事的地方：加个新功能把老功能搞坏。
    """
    from app.agent.mcp import registry as reg_mod

    broken = MCPRegistry({"bad": ["/nonexistent/binary/xyz"]})
    monkeypatch.setattr(reg_mod, "mcp_servers", broken)
    import app.agent.tools as tools_mod

    monkeypatch.setattr(tools_mod, "mcp_servers", broken)

    tools = await schemas()
    names = [t["function"]["name"] for t in tools]
    for local in ALL_TOOLS:
        assert local.name in names
    # 坏服务器的工具不在清单里，但也没抛异常
    assert not any(n.startswith("mcp__bad__") for n in names)


# ── execute 路由 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_routes_mcp_call(fresh_registry) -> None:
    out = await execute("mcp__weather__get_weather", '{"city": "北京"}')
    assert "北京" in out
    assert "晴" in out


@pytest.mark.asyncio
async def test_execute_routes_local_call(fresh_registry) -> None:
    """加了 MCP 之后，本地工具还得能用。"""
    out = await execute("bash", '{"command": "echo mcp_tests_ok"}')
    assert "mcp_tests_ok" in out


@pytest.mark.asyncio
async def test_execute_handles_bad_json_for_mcp(fresh_registry) -> None:
    """模型经常把参数拼坏。这不该让对话崩，要把错误交给它自己修。"""
    out = await execute("mcp__weather__get_weather", "{not json")
    assert "不是合法 JSON" in out


@pytest.mark.asyncio
async def test_execute_handles_non_object_arguments(fresh_registry) -> None:
    """参数是数组/字符串时，本地工具和 MCP 的处理要一致。"""
    out = await execute("mcp__weather__get_weather", '["北京"]')
    assert "必须是对象" in out


@pytest.mark.asyncio
async def test_execute_unknown_mcp_tool_returns_message(fresh_registry) -> None:
    """不存在的 MCP 工具：返回错误文字，不抛异常。"""
    out = await execute("mcp__weather__nope", "{}")
    assert "错误" in out or "出错" in out


@pytest.mark.asyncio
async def test_execute_unknown_server_returns_message(fresh_registry) -> None:
    """配置里没有的服务器名，也要有可读的错误。"""
    out = await execute("mcp__ghost__do_thing", "{}")
    assert "没有配置" in out or "错误" in out


# ── 生命周期 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_kills_children(fresh_registry) -> None:
    """关掉注册表后，所有子进程都要消失。"""
    await schemas()                     # 触发懒启动
    clients = list(fresh_registry._clients.values())
    assert clients, "应该已经起了连接"
    procs = [c._proc for c in clients]

    await fresh_registry.aclose()

    for proc in procs:
        assert proc is not None
        assert proc.returncode is not None, "aclose 之后子进程还活着"


@pytest.mark.asyncio
async def test_mcp_tool_list_is_cached(fresh_registry) -> None:
    """MCP 的清单要缓存 —— 每次请求模型都去问一遍太浪费（一次进程往返）。

    注意缓存的是 MCP 那一半，不是 `schemas()` 的返回值：
    本地工具每次都新建列表（便宜），MCP 那半是复用的。
    """
    first = await fresh_registry.schemas()
    assert first, "应该已经拉到工具"
    second = await fresh_registry.schemas()
    assert second is first, "第二次应该直接返回缓存"


@pytest.mark.asyncio
async def test_aclose_clears_cache(fresh_registry) -> None:
    """关掉之后缓存要清空，否则重连会拿到旧服务器的工具。"""
    await fresh_registry.schemas()
    await fresh_registry.aclose()
    assert fresh_registry._tools is None
