"""agent 核心 —— 整个项目的心脏。

S1 时它只调用一次模型。S3 起它变成一个循环：

    调用模型 → 它说要调工具？→ 执行 → 结果塞回对话 → 再调用
             → 它不调了？→ 结束

这就是 ReAct（Reason + Act）的全部。所谓 "agent 框架"，
剥开封装后跑的就是下面这个 while。

真正难的不是循环，是三件事：
    ① 工具调用碎片怎么拼（流式分多帧，见 delta.py）
    ② tool_call_id 怎么配对（结果必须挂回对应调用）
    ③ 什么时候刹车（模型可能一直调工具，永不停下）
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.config import settings
from .events import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    ReasoningEvent,
    StartEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .llm.client import explain, stream_deltas
from .prompt import system_prompt
from .tools import execute

type Message = dict[str, Any]

# 刹车：一次提问最多循环几轮。8 轮够做完"读文件→改文件→跑测试"，
# 又不至于让模型进入无限自我对话白白烧钱。
MAX_ROUNDS = 8


class _Accumulator:
    """把流式碎片拼成完整的工具调用。

    为什么需要单独一个类：拼接逻辑有状态（按 index 分组），
    塞进 chat() 会让主流程看不清。这也是 S3 里最容易出 bug 的地方 ——
    串行的 arguments 必须**字符串相加**，不能用后面的覆盖前面的。
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, deltas: list[Any]) -> None:
        for d in deltas:
            slot = self._calls.setdefault(
                d.index, {"id": "", "name": "", "arguments": ""}
            )
            # id / name 通常只在第一帧出现，后来的空值不能覆盖掉
            if d.id:
                slot["id"] = d.id
            if d.name:
                slot["name"] = d.name
            slot["arguments"] += d.arguments

    def result(self) -> list[dict[str, str]]:
        """按 index 排序输出，让执行顺序稳定可预期。"""
        return [self._calls[i] for i in sorted(self._calls)]

    def __bool__(self) -> bool:
        return bool(self._calls)


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
    total_chars = 0
    finish_reason: str | None = None
    rounds = 0

    # 复制一份：后面会往里追加 assistant / tool 消息。
    # 如果直接改调用方传进来的 list，多轮对话的历史会被污染。
    convo: list[Message] = list(messages)

    # 系统提示词只加一次。调用方已经带了就不重复插 ——
    # 多轮对话时 messages 里可能已经有历史，别把它挤掉。
    if not convo or convo[0].get("role") != "system":
        convo.insert(0, {"role": "system", "content": system_prompt()})

    yield StartEvent(
        model="mock" if settings.is_mock else settings.llm_model,
        mock=settings.is_mock,
    )

    try:
        while rounds < MAX_ROUNDS:
            rounds += 1
            acc = _Accumulator()
            round_text = ""

            async for delta in stream_deltas(convo):
                if delta.reasoning:
                    yield ReasoningEvent(delta=delta.reasoning)

                if delta.text:
                    round_text += delta.text
                    total_chars += len(delta.text)
                    yield TextEvent(delta=delta.text)

                if delta.tool_calls:
                    acc.feed(delta.tool_calls)

                if delta.finish_reason:
                    finish_reason = delta.finish_reason

            # ── 没有工具调用 → 这次回答就结束了 ──────────────
            if not acc:
                break

            calls = acc.result()

            # ① 先把"模型想做什么"记进对话历史。
            #    tool_calls 必须原样保留：下一轮请求里，
            #    模型要靠 tool_call_id 找到自己刚才的意图。
            convo.append(
                {
                    "role": "assistant",
                    "content": round_text,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]},
                        }
                        for c in calls
                    ],
                }
            )

            # ② 逐个执行，结果作为 role="tool" 的消息塞回去
            for call in calls:
                yield ToolCallEvent(
                    id=call["id"], name=call["name"], arguments=call["arguments"]
                )

                output = await execute(call["name"], call["arguments"])
                ok = not output.startswith(("错误：", "工具执行出错：", "拒绝执行："))

                yield ToolResultEvent(
                    id=call["id"], name=call["name"], ok=ok, output=output
                )

                convo.append(
                    {
                        "role": "tool",
                        # 这个 id 就是配对的关键：模型据此知道
                        # 这份结果属于哪一次调用
                        "tool_call_id": call["id"],
                        "content": output,
                    }
                )
            # ③ 回到 while 顶部：带着工具结果再问模型一次

        else:
            # while 正常跑满 MAX_ROUNDS 才会走到这里（没 break）
            yield ErrorEvent(
                message=f"已连续调用工具 {MAX_ROUNDS} 轮仍未结束，强制停止。"
                "可能是任务太大，或是模型在原地打转。"
            )

    except asyncio.CancelledError:
        # 用户关了页面 / 点了停止。必须重新抛出，
        # 否则取消信号被吞掉，上层会以为任务正常结束了。
        raise
    except Exception as exc:
        yield ErrorEvent(message=explain(exc))
        return

    yield DoneEvent(
        finish_reason=finish_reason,
        ms=int((time.perf_counter() - started) * 1000),
        chars=total_chars,
        rounds=rounds,
    )
