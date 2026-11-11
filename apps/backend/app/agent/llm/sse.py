"""SSE 分帧：把「行」拼成「帧」。

整个项目最容易写错的一段，所以单独一个文件、单独讲一次。

**只需记住一句话：帧的边界是空行，不是换行。**

一帧可能长这样（多行 data 属于同一帧）：

    data: {"choices":[{"delta":{"content":"你"}}]}

    data: {...}

    data: [DONE]

httpx 的 aiter_lines() 只帮你切成行，"帧"这个概念得自己攒。
"""

from __future__ import annotations

from collections.abc import AsyncIterator


async def iter_frames(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """行流 → 帧流。每吐出一个字符串，就是**一帧的 data 部分**。

    用法：
        async for raw in iter_frames(resp.aiter_lines()):
            if raw == "[DONE]":
                return
            obj = json.loads(raw)
    """
    buffer: list[str] = []

    async for line in lines:
        if line != "":
            # 只收 data: 开头的行；event:/id:/retry: 这类我们先不管
            if line.startswith("data:"):
                # 冒号后可能没有空格（"data:x"），所以先切再 lstrip
                buffer.append(line[5:].lstrip())
            continue

        # 走到这里说明读到空行 = 一帧结束
        if buffer:
            yield "\n".join(buffer)
            buffer.clear()

    # 少数服务端结尾不加空行，收尾时把剩下的半帧也吐出去
    if buffer:
        yield "\n".join(buffer)