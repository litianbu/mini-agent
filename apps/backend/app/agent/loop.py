"""agent 核心 —— 整个项目的心脏。

真正干的活只有 25 行（下面那个 try 块），其余都是注释和包装。
别被行数吓到：复杂的是"为什么这么设计"，不是代码本身。

现在它还很薄：一次调用 + 一层事件包装。
但结构是对的：它产出事件，而不是字符串。
等 S3 加了循环、S4 加了工具，这里会长出 while 和 tool 分发，
而 api/ 和 cli.py 一行都不用改。

这就是"先定事件契约，再写实现"值得多花 10 分钟的原因。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from ..core.config import settings
from .events import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    ReasoningEvent,
    StartEvent,
    TextEvent,
)
from .llm.client import explain, stream_deltas

type Message = dict[str, str]


async def chat(
    messages: list[Message],
    *,
    thinking: bool | None = None,
) -> AsyncIterator[AgentEvent]:
    """一次对话 = 一串事件。

    调用方只需要写：
        async for event in chat(messages):
            ...

    它不关心底层是 SSE、是 WebSocket，还是别的什么。
    """
    started = time.perf_counter()
    chars = 0
    finish_reason: str | None = None

    # ① 先报到：前端要立刻知道"开始了"以及用的是什么模型
    yield StartEvent(
        model="mock" if settings.is_mock else settings.llm_model,
        mock=settings.is_mock,
    )

    try:
        # ② 核心：把模型的增量翻译成事件
        async for delta in stream_deltas(messages):
            if delta.reasoning:
                yield ReasoningEvent(delta=delta.reasoning)

            if delta.text:
                chars += len(delta.text)
                yield TextEvent(delta=delta.text)

            if delta.finish_reason:
                finish_reason = delta.finish_reason

    except asyncio.CancelledError:
        # 用户关了页面 / 点了停止。这里必须重新抛出，
        # 否则取消信号被吞掉，上层会以为任务正常结束了。
        raise
    except Exception as exc:
        # ③ 出错也是事件 —— 流已经开始了，HTTP 状态码改不了，
        #    唯一的通知渠道就是流本身。
        yield ErrorEvent(message=explain(exc))
        return

    # ④ 收尾：报一下耗时和字数，前端拿它显示"完成 · 101 字 · 2327ms"
    yield DoneEvent(
        finish_reason=finish_reason,
        ms=int((time.perf_counter() - started) * 1000),
        chars=chars,
    )
