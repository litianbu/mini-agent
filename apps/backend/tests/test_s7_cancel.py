"""取消传播：客户端断开时，后端要立刻停手。

这个模块存在的意义只有一个 —— **在没有事件产出的时候也能切断**。

原来的写法是轮询：
    async for event in chat(convo):
        if await request.is_disconnected(): return

它只在"有事件"时执行。可是最该切断的时刻恰恰是没有事件的时候：
模型在思考、或者卡死，一个字都不吐，循环停在 await 上，
那行检查永远走不到 —— 后端继续为用户烧钱，直到 300 秒超时。

所以这里的测试重点不是"正常时能跑完"，而是：
**产流永远不出东西时，断开信号能不能把它切掉。**
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.streaming import bridge


class FakeRequest:
    """假的 Request，只需要 is_disconnected()。

    @param disconnect_after  被问第几次之后返回 True（None = 永不返回 True）
    """

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._after = disconnect_after
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self._after is not None and self.calls > self._after


async def _collect(agen) -> list[str]:
    return [item async for item in agen]


# ── 正常路径 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yields_everything_then_stops() -> None:
    """不该丢数据，也不该卡住。"""

    async def source():
        for i in range(3):
            yield f"块{i}"

    assert await _collect(bridge(source(), FakeRequest())) == ["块0", "块1", "块2"]


@pytest.mark.asyncio
async def test_source_closes_cleanly() -> None:
    """产流器的 finally 必须执行 —— 上游靠它关连接、存半截数据。"""
    cleaned = False

    async def source():
        nonlocal cleaned
        try:
            yield "a"
        finally:
            cleaned = True

    await _collect(bridge(source(), FakeRequest()))
    assert cleaned, "产流器的收尾代码没跑"


@pytest.mark.asyncio
async def test_exception_from_source_reaches_consumer() -> None:
    """产流出错要抛给调用方，不能静默吞掉。"""

    async def source():
        yield "a"
        raise RuntimeError("模型炸了")

    with pytest.raises(RuntimeError, match="模型炸了"):
        await _collect(bridge(source(), FakeRequest()))


# ── 断开：这才是这个模块的意义 ───────────────────────────


@pytest.mark.asyncio
async def test_disconnect_stops_a_silent_source() -> None:
    """产流一直不说话时，断开信号也要能把它切掉。

    这是原实现做不到的事 —— 数组里没有事件，就没有检查机会。
    """
    cancelled = False

    async def silent():
        nonlocal cancelled
        try:
            await asyncio.sleep(3600)   # 模拟模型卡住，一个字都不吐
            yield "永远到不了"
        except asyncio.CancelledError:
            cancelled = True
            raise

    # 问 1 次之后就"断开"
    req = FakeRequest(disconnect_after=1)

    async def run() -> None:
        async for _ in bridge(silent(), req, interval=0.05):
            pytest.fail("不该收到任何数据")

    # 5 秒内必须结束。原来会等 3600 秒。
    await asyncio.wait_for(run(), timeout=5)

    assert cancelled, "产流任务没被取消，说明取消信号没传下去"


@pytest.mark.asyncio
async def test_disconnect_during_active_stream() -> None:
    """正在吐数据时断开，也要尽快停。"""

    async def endless():
        i = 0
        while True:
            yield f"块{i}"
            i += 1
            await asyncio.sleep(0.01)

    req = FakeRequest(disconnect_after=1)   # 第一次问就说断开

    got = []

    async def run() -> None:
        async for item in bridge(endless(), req, interval=0.05):
            got.append(item)

    await asyncio.wait_for(run(), timeout=5)

    # 断开后不该继续无限产出
    assert len(got) < 100, f"断开后还在继续产出（拿到 {len(got)} 条）"


@pytest.mark.asyncio
async def test_consumer_break_cancels_producer() -> None:
    """消费端主动 break（比如 HTTP 层关掉生成器），产流也要停。"""
    cancelled = False

    async def endless():
        nonlocal cancelled
        try:
            i = 0
            while True:
                yield f"块{i}"
                i += 1
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def run() -> None:
        async for _ in bridge(endless(), FakeRequest()):
            break       # 拿一条就走

    await asyncio.wait_for(run(), timeout=5)

    # 给事件循环一点时间跑 finally
    await asyncio.sleep(0.1)
    assert cancelled, "消费端退出后，产流任务应该被取消"


@pytest.mark.asyncio
async def test_no_leftover_tasks() -> None:
    """结束后不能留下野任务。

    留了的话，事件循环关闭时会报 "Task was destroyed but it is pending"，
    而且那些任务还在后台跑（白烧钱）。
    """
    before = len(asyncio.all_tasks())

    async def source():
        for i in range(3):
            yield f"块{i}"

    await _collect(bridge(source(), FakeRequest()))

    # 让被取消的任务有机会收尾
    await asyncio.sleep(0.1)
    after = len(asyncio.all_tasks())

    assert after <= before, f"留下了 {after - before} 个任务"
