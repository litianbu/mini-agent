"""pytest 全局配置。

目前只有一件事：**测试跑完后保证 MCP 子进程不残留**。

为什么需要这个兜底：
    `schemas()` 会懒启动 MCP 子进程（第一次调用时才起）。
    调了 schemas() 的测试如果没自己收尾，进程就留在系统里 ——
    跑完全套一查 pgrep，一堆 `python -m weather_server`，
    还得手动 pkill，而且会干扰下一次测试。

**为什么用"杀进程"而不是"优雅关闭"**：
    优雅关闭要 await，那就得有一个异步 fixture；
    但 pytest-asyncio 的 session 级异步 fixture 会在
    不同的事件循环之间打架（"attached to a different loop"）。
    收尾这件事不需要优雅 —— 测试都跑完了，进程直接杀干净就行。
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture(scope="session", autouse=True)
def _cleanup_mcp_at_exit():
    """整个测试会话结束后，把残留的 MCP 服务器进程清理掉。"""
    yield

    # 只杀我们自己的那个模块，不会误伤别的 python 进程
    subprocess.run(
        ["pkill", "-f", "app.agent.mcp.weather_server"],
        capture_output=True,
    )
