"""bash 工具被取消时，要真的杀掉子进程。

为什么单独测这个：
    asyncio.wait_for 超时会取消 communicate()，但**不会**杀掉进程。
    cancel 同理 —— 它只打断"等"，进程还在跑。

    结果是：模型跑一个 `sleep 1000`，用户点停止，
    协程结束了，sleep 还在后台占 CPU，而且没人看得到它的输出。这叫孤儿进程。
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from app.agent.tools.shell import run_bash


async def _run_and_cancel(command: str, wait: float = 0.4) -> None:
    """把一次工具执行跑在任务里，等它进入等待后取消。"""
    task = asyncio.create_task(run_bash({"command": command}))
    await asyncio.sleep(wait)          # 让它真的把进程拉起来
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _alive(marker: str) -> bool:
    """用唯一标记查进程是否还在（pgrep 比 ps 稳）。"""
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return bool(out.stdout.strip())


def _force_kill(marker: str) -> None:
    subprocess.run(["pkill", "-f", marker], capture_output=True)


@pytest.mark.asyncio
async def test_cancel_kills_the_child_process() -> None:
    """取消之后，子进程必须消失。

    用带小数的 sleep 时长做唯一标记 —— 别的测试或用户进程
    不会刚好跑 `sleep 30.123456`，所以 pgrep 不会误判。
    """
    marker = "sleep 30.654321"
    _force_kill(marker)          # 清理上次残留

    try:
        await _run_and_cancel(marker)

        # 给内核一点时间回收
        await asyncio.sleep(0.3)
        assert not _alive(marker), "取消后子进程还在跑 —— 孤儿进程"
    finally:
        _force_kill(marker)


@pytest.mark.asyncio
async def test_cancel_still_propagates() -> None:
    """杀进程之后，取消信号必须继续往外传。

    这里有个坑：如果在 except CancelledError 里吞掉异常（不 raise），
    上层会以为工具正常结束了，于是继续跑下一轮 —— 用户明明点了停止。

    表现就是：点了停止，界面还在继续出内容。
    """
    marker = "sleep 30.123123"
    _force_kill(marker)

    task = asyncio.create_task(run_bash({"command": marker}))
    await asyncio.sleep(0.4)
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        _force_kill(marker)


@pytest.mark.asyncio
async def test_timeout_also_kills() -> None:
    """超时路径（原本就有）顺带回归一下，别被改坏。"""
    marker = "sleep 30.777777"
    _force_kill(marker)

    try:
        result = await run_bash({"command": marker, "timeout": 1})
        assert "超时" in result

        await asyncio.sleep(0.3)
        assert not _alive(marker), "超时后子进程还在跑"
    finally:
        _force_kill(marker)


@pytest.mark.asyncio
async def test_normal_command_still_works() -> None:
    """加了 kill 逻辑不能影响正常执行。"""
    result = await run_bash({"command": "echo hello"})
    assert "hello" in result
