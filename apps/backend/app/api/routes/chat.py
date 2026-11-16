"""对话接口：一问一答，响应是 SSE 事件流，同时把这一轮存进数据库。

这里只做 HTTP 相关的事：读请求、查连接有没有断、存库、把事件流包成响应。
"模型怎么想"的逻辑全在 agent/loop.py 里。

存库的时机有个讲究：
    用户消息在**流开始前**就存 —— 万一模型挂了，至少知道用户问了什么。
    助手消息在**流结束后**存 —— 必须等收齐才能拿到完整回答。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent.events import StartEvent, TextEvent, ReasoningEvent, to_sse
from ...agent.loop import chat
from ...schemas.chat import ChatRequest
from ...services import session_service
from ..deps import get_db

router = APIRouter()


@router.post("/chat")
async def post_chat(
    body: ChatRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    # ① 先定下会话：给了 id 就接着聊，没给就新建一个
    if body.session_id is not None:
        session = await session_service.get_session(db, body.session_id)
        if session is None:
            # 让前端能区分"会话没了"和"模型出错"，而不是默默开一个新的
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
    else:
        # 标题先用提问的开头，和大多数聊天软件的做法一致
        session = await session_service.create_session(db, body.question[:30])

    session_id = session.id

    # ② 历史消息 + 这次提问，一起交给 agent
    history = await session_service.messages_for_llm(db, session_id)
    convo = [*history, {"role": "user", "content": body.question}]

    # 用户消息先落库（此时还不知道回答是什么）
    await session_service.add_message(db, session_id, "user", body.question)

    async def event_stream() -> AsyncIterator[str]:
        # 助手的回答边流边攒，结束时一次性写入
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finished = False

        async for event in chat(convo):
            # 客户端断开（用户点了「停止」或关掉页面）就别接着生成 ——
            # 每帧查一次，代价可忽略，省下的可能是真金白银。
            if await request.is_disconnected():
                return

            # start 事件补上 session_id，前端据此把地址栏切到 /sessions/123
            if isinstance(event, StartEvent):
                event = event.model_copy(update={"session_id": session_id})
            elif isinstance(event, TextEvent):
                text_parts.append(event.delta)
            elif isinstance(event, ReasoningEvent):
                reasoning_parts.append(event.delta)
            elif event.type == "done":
                finished = True

            yield to_sse(event)

        # ③ 收尾存库。即使用户中途断开（上面 return 了）也算，见 finally
        if finished:
            await _save_answer(db, session_id, text_parts, reasoning_parts)

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


async def _save_answer(
    db: AsyncSession,
    session_id: int,
    text_parts: list[str],
    reasoning_parts: list[str],
) -> None:
    """把助手的完整回答写进库。空回答不写（比如纯工具调用后没出文字）。"""
    content = "".join(text_parts).strip()
    reasoning = "".join(reasoning_parts).strip()
    if not content and not reasoning:
        return
    await session_service.add_message(
        db, session_id, "assistant", content, reasoning=reasoning or None
    )
