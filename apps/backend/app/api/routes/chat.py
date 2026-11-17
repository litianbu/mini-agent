"""对话接口：一问一答，响应是 SSE 事件流，同时把这一轮存进数据库。

这里只做 HTTP 相关的事：读请求、查连接有没有断、存库、把事件流包成响应。
"模型怎么想"的逻辑全在 agent/loop.py 里。

存库的时机有个讲究：
    用户消息在**流开始前**就存 —— 万一模型挂了，至少知道用户问了什么。
    助手消息在**流结束后**存 —— 必须等收齐才能拿到完整回答。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent.events import (
    ReasoningEvent,
    StartEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    to_sse,
)
from ...agent.loop import chat
from ...schemas.chat import ChatRequest
from ...core.limiter import session_limiter
from ...services import session_service
from ..deps import get_db
from ..streaming import bridge

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

    # ② 抢占这个会话。上一条还在生成就拒绝，回 429。
    #
    # 位置很关键：必须在"写用户消息"之前。
    # 否则被拒绝的请求也会把提问存进库，留下一条永远没有回答的孤消息。
    if not session_limiter.acquire(session_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="这个会话的上一条回答还在生成，请稍候再发",
        )

    try:
        # 历史消息 + 这次提问，一起交给 agent
        history = await session_service.messages_for_llm(db, session_id)
        convo = [*history, {"role": "user", "content": body.question}]
        print('convo 测试起来', convo)
        # 用户消息先落库（此时还不知道回答是什么）
        await session_service.add_message(db, session_id, "user", body.question)
    except Exception:
        # 抢到锁之后、生成器启动之前出错，得在这里放掉，
        # 否则这个会话就永远卡在"有人在跑"的状态
        session_limiter.release(session_id)
        raise

    async def event_stream() -> AsyncIterator[str]:
        try:
            # bridge 负责两件事：边产边发、盯着客户端断开。
            # 断开时它会取消产流任务 —— 取消信号一路传到 httpx 连接，
            # 模型那边立刻停，不会继续为已经离开的用户烧钱。
            async for chunk in bridge(_stream(), request):
                yield chunk
        finally:
            # 不管是正常结束、出错、还是用户断开，都要放锁。
            # 这是唯一可靠的释放点 —— 生成器被关闭时也会执行。
            session_limiter.release(session_id)

    async def _stream() -> AsyncIterator[str]:
        # 一轮对话 = 可能多轮"模型 → 工具 → 模型"。
        # 边流边攒，等到该落库的时机再写。
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: list[dict] = []        # 本轮模型要求调用的工具
        round_saved = False          # 本轮的 assistant 行是否已经写过
        finished = False

        async def flush_assistant(tool_calls: str | None = None) -> None:
            """写一条 assistant 消息（正文可能为空，工具调用在 tool_calls 里）。"""
            await session_service.add_message(
                db,
                session_id,
                "assistant",
                "".join(text_parts).strip(),
                reasoning="".join(reasoning_parts).strip() or None,
                tool_calls=tool_calls,
            )

        def start_new_round() -> None:
            """进入下一轮：清空本轮的缓冲区。"""
            nonlocal round_saved
            text_parts.clear()
            reasoning_parts.clear()
            calls.clear()
            round_saved = False

        async for event in chat(convo):
            # 断开检测不在这里做了 —— 交给 streaming.bridge。
            # 原因：这一行只有在"产出新事件"时才执行，
            # 而最需要切断的时刻恰恰是模型不产出任何事件的时候。
            if isinstance(event, StartEvent):
                # start 事件补上 session_id，前端据此把地址栏切到 /sessions/123
                event = event.model_copy(update={"session_id": session_id})

            elif isinstance(event, TextEvent):
                if round_saved:
                    start_new_round()
                text_parts.append(event.delta)

            elif isinstance(event, ReasoningEvent):
                if round_saved:
                    start_new_round()
                reasoning_parts.append(event.delta)

            elif isinstance(event, ToolCallEvent):
                if round_saved:
                    start_new_round()
                calls.append(
                    {
                        "id": event.id,
                        "type": "function",
                        "function": {"name": event.name, "arguments": event.arguments},
                    }
                )

            elif isinstance(event, ToolResultEvent):
                # 第一条结果到达时，先把"声明调用"的那条 assistant 写掉。
                # 一个 assistant 行可以声明多个 tool_calls（模型并行调工具）。
                if not round_saved:
                    await flush_assistant(
                        json.dumps(calls, ensure_ascii=False) if calls else None
                    )
                    round_saved = True

                # 再写这条工具结果。tool_call_id 是配对的唯一凭据
                await session_service.add_message(
                    db, session_id, "tool", event.output, tool_call_id=event.id
                )

            elif event.type == "done":
                finished = True

            yield to_sse(event)

        # 收尾：把最后一条 assistant 写掉（正常回答，没有更多工具调用）
        # 即使用户中途断开（上面 return 了）也走不到这里，见 finally 说明
        if finished:
            await flush_assistant()

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
