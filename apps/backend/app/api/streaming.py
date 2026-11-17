"""把"产流"和"监听断开"拆成两个任务，谁先结束就取消另一个。

**原来的写法为什么不够**：

    async for event in chat(convo):
        if await request.is_disconnected():   # ← 必须先拿到事件才检查
            return

这是**轮询**：只有产出新事件时才有机会检查。可是最需要切断的时刻，
恰恰是**没有事件**的时候：

    用户点「停止」→ 关掉页面
    → 模型侧还在思考（或卡死），一个字都不吐
    → chat.py 卡在 async for 上，永远走不到那行检查
    → 后端继续等，直到总超时（300 秒）才释放

结果是：用户以为停了，服务器还在为他烧钱。

**这里的做法**：生成跑在独立的 Task 里，断开检测也跑在独立 Task 里。
检测到断开就把「结束信号」塞进队列，主循环立刻退出，
`finally` 里把还在跑的那个 Task `cancel()` 掉 —— 取消信号会一层层传下去：

    bridge 的 producer Task
      → _stream() 生成器
        → chat() 生成器
          → stream_deltas() 的 httpx 流  ← 连接在这里关掉
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

__all__ = ["bridge"]

# 队列里的结束标记。用独有对象而不是 None ——
# None 可能是合法的数据，标记不能和数据混淆。
_END = object()

# 断开检测的间隔。0.5 秒够灵敏，又不至于把 CPU 空转起来。
_POLL_INTERVAL = 0.5


async def _cancel_all(tasks: list[asyncio.Task[Any]]) -> None:
    """取消一批任务，并等它们真的结束。

    **必须等**：只调 cancel() 就返回的话，任务还在后台收尾，
    事件循环关闭时会报 "Task was destroyed but it is pending"。
    """
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except BaseException:
            # 这里吞掉是安全的：任务已经取消，异常没有别的接收者了。
            # 注意 BaseException 而不是 Exception —— CancelledError
            # 不是 Exception 的子类（3.8+），用 Exception 兜不住。
            pass


async def bridge(
    source: AsyncIterator[str],
    request: Any,
    *,
    interval: float = _POLL_INTERVAL,
) -> AsyncIterator[str]:
    """边产边发，同时盯着客户端是否断开。

    @param source   产流器（不能自己 await 断开，它只管生成）
    @param request  Starlette 的 Request，只用它的 is_disconnected()
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError:
            raise  # 取消要往外传，不能当成普通异常处理
        except Exception as exc:  # noqa: BLE001
            # 产流出错也要让消费端知道，不能静默吞掉
            await queue.put(exc)
        finally:
            await queue.put(_END)

    async def watch() -> None:
        while True:
            if await request.is_disconnected():
                # 客户端走了。塞结束标记，主循环会在下一轮退出。
                await queue.put(_END)
                return
            await asyncio.sleep(interval)

    producer = asyncio.create_task(produce())
    watcher = asyncio.create_task(watch())

    try:
        while True:
            item = await queue.get()
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        # 三种退出路径都会走到这：正常结束、异常、客户端断开。
        # 无论哪种，都要保证没有任务在后台继续跑。
        await _cancel_all([producer, watcher])
