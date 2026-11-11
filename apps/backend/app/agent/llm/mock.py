"""假模型：没有 API key 时的替身。

它不是玩具 —— 它逐字吐字、也有思考过程、也会正常结束。
所以前端开发、演示、自动化测试都不需要网络，也不需要花钱。

写它的另一个意义：它就是一份"真模型该长什么样"的规范。
哪天你接别家的接口，只要产出同样的 Delta 序列，上层一行都不用改。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..delta import Delta

# 打字机节奏：思考快一点，正文慢一点，肉眼能看出"两个阶段"
THINK_DELAY = 0.008
TEXT_DELAY = 0.02


async def mock_deltas(messages: list[dict]) -> AsyncIterator[Delta]:
    """把最后一条用户消息回显一遍，中间插一段假的思考过程。"""
    question = _last_user_message(messages)

    reasoning = (
        "用户问的是：" + (question[:40] or "（空）") + "。我先想想怎么回答才清楚……"
    )
    answer = (
        f"（MOCK 模式）你说的是「{question}」。"
        "我这边没有 API key，所以返回的是本地假数据 —— "
        "但流式管道、事件契约、前端渲染都是真的在跑。"
        "填上 DEEPSEEK_API_KEY 再试一次，就是真模型了。"
    )

    for ch in reasoning:
        await asyncio.sleep(THINK_DELAY)
        yield Delta(reasoning=ch)

    for ch in answer:
        await asyncio.sleep(TEXT_DELAY)
        yield Delta(text=ch)

    # 结束标记：和真模型一样，用最后一帧告诉上层"我说完了"
    yield Delta(finish_reason="stop")


def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""
