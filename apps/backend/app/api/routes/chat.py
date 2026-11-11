"""对话接口：一问一答，响应是 SSE 事件流。

这里只做 HTTP 相关的事：读请求、查连接有没有断、把事件流包成响应。
任何"模型怎么想"的逻辑都不该出现在这个文件里。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ...agent.events import to_sse
from ...agent.loop import chat
from ...schemas.chat import ChatRequest

router = APIRouter()


@router.post("/chat")
async def post_chat(body: ChatRequest, request: Request) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        async for event in chat([{"role": "user", "content": body.question}]):
            # 客户端断开（用户点了「停止」或关掉页面）就别接着生成 ——
            # 每帧查一次，代价可忽略，省下的可能是真金白银。
            if await request.is_disconnected():
                return
            yield to_sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # 这三个头是 SSE 的老三样，少一个就可能"攒一批再发"
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )