"""会话的读写。

为什么单独一层（不放路由里）：
    路由管 HTTP，agent 管推理，中间的"怎么存"不该混进任何一边。
    放在这里的好处是 CLI 和 HTTP 能共用同一套逻辑，
    将来换存储（JSONL / Redis）也只改这一个文件。
"""

from __future__ import annotations

import json

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import ChatSession, Message

# 侧栏最多显示这么多条，再多也没人翻
MAX_SESSIONS = 100


async def create_session(db: AsyncSession, title: str = "") -> ChatSession:
    """新建一个会话。标题留空就用"新会话"，稍后由第一条提问补上。"""
    session = ChatSession(title=(title.strip() or "新会话")[:200])
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, session_id: int) -> ChatSession | None:
    """取会话（不含消息）。"""
    return await db.get(ChatSession, session_id)


async def list_sessions(db: AsyncSession) -> list[tuple[ChatSession, int]]:
    """侧栏用的列表：每个会话 + 它有多少条消息。

    用一次 join + group by 拿到条数，不要"先查会话再逐个 count" ——
    那是 N+1 查询，20 个会话就是 21 次数据库往返。
    """
    stmt = (
        select(ChatSession, func.count(Message.id))
        .outerjoin(Message, Message.session_id == ChatSession.id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(MAX_SESSIONS)
    )
    rows = await db.execute(stmt)
    return [(row[0], row[1]) for row in rows.all()]


async def list_messages(db: AsyncSession, session_id: int) -> list[Message]:
    """取一个会话的全部消息，按 id 升序（也就是时间顺序）。"""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def add_message(
    db: AsyncSession,
    session_id: int,
    role: str,
    content: str,
    *,
    reasoning: str | None = None,
    tool_calls: str | None = None,
    tool_call_id: str | None = None,
) -> Message:
    """追加一条消息，并把会话的 updated_at 顶上去（侧栏靠它排序）。"""
    msg = Message(
        session_id=session_id,
        role=role,
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )
    db.add(msg)

    session = await db.get(ChatSession, session_id)
    if session is not None:
        # 显式赋值，触发 onupdate；否则只有消息变了，会话不会排到前面
        session.updated_at = func.now()

    await db.commit()
    await db.refresh(msg)
    return msg


async def rename_session(db: AsyncSession, session_id: int, title: str) -> None:
    """改标题。前端目前只在建会话时自动起名，这个接口留给以后手动改。"""
    session = await db.get(ChatSession, session_id)
    if session is not None:
        session.title = title.strip()[:200] or "新会话"
        await db.commit()


async def delete_session(db: AsyncSession, session_id: int) -> bool:
    """删会话。消息靠外键的 ON DELETE CASCADE 一起删。"""
    result = await db.execute(delete(ChatSession).where(ChatSession.id == session_id))
    await db.commit()
    return result.rowcount > 0


async def messages_for_llm(db: AsyncSession, session_id: int) -> list[dict]:
    """把库里的消息还原成模型认识的形状（OpenAI messages）。

    S5 时这里只取 user/assistant 正文，工具调用被整个跳过 ——
    因为那时工具消息没落库，硬拼一个残缺的 tool 消息会让上游报错。
    现在工具调用落库了，可以完整还原。

    **配对是硬约束**：assistant 的 tool_calls 和随后的 role=tool
    必须成对出现。少一半，上游直接 400。所以下面用 known 集合把关：
    只有 id 确实被某个 assistant 声明过的 tool 消息才放行。
    """
    rows = await list_messages(db, session_id)

    out: list[dict] = []
    known: set[str] = set()  # 已经被声明过的工具调用 id

    for m in rows:
        if m.role == "user":
            if m.content:
                out.append({"role": "user", "content": m.content})

        elif m.role == "assistant":
            item: dict = {"role": "assistant", "content": m.content or ""}

            if m.tool_calls:
                try:
                    calls = json.loads(m.tool_calls)
                except json.JSONDecodeError:
                    # 坏数据不猜，跳过这一条，免得整个会话都发不出去
                    calls = []
                if calls:
                    item["tool_calls"] = calls
                    known.update(c.get("id") for c in calls if c.get("id"))

            # 有正文或有工具调用的才留。工具调用的那条 content 常常是空的。
            # 注意用 strip() 判断：纯空格的 content 在 Python 里是"真值"，
            # 但发给上游一样会被拒（"content must not be empty"）。
            if item["content"].strip() or item.get("tool_calls"):
                out.append(item)

        elif m.role == "tool":
            # id 没被声明过 = 半截数据，发出去会 400，宁可丢掉
            if m.tool_call_id and m.tool_call_id in known:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )

    return out