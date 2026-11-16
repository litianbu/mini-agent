"""会话的增删查接口。

和 /api/chat 的分工：
    /api/chat    只管流式对话（顺便把消息存库）
    /api/sessions 管"读历史"：列表、详情、删除
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.session import MessageOut, SessionDetail, SessionSummary
from ...services import session_service
from ..deps import get_db

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def get_sessions(db: AsyncSession = Depends(get_db)) -> list[SessionSummary]:
    rows = await session_service.list_sessions(db)
    return [
        SessionSummary(
            id=s.id,
            title=s.title,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=count,
        )
        for s, count in rows
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: int, db: AsyncSession = Depends(get_db)
) -> SessionDetail:
    session = await session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")

    messages = await session_service.list_messages(db, session_id)
    return SessionDetail(
        id=session.id,
        title=session.title,
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)) -> None:
    if not await session_service.delete_session(db, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
