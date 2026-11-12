"""消息表：会话里的每一条（用户问的、助手答的、工具结果）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, LongText


class Message(Base):
    """一条消息。

    role 和 OpenAI messages 的 role 对齐（user / assistant / tool），
    这样从库里读出来能直接喂回模型，不用做转换。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )

    # user / assistant / tool / system
    role: Mapped[str] = mapped_column(String(20))

    # 正文。助手带工具调用时这一条可能为空（内容在 tool_calls 里）
    content: Mapped[str] = mapped_column(LongText, default="")

    # 工具调用的 JSON 串（助手消息才有），以及它配对的调用 id（tool 消息才有）
    tool_calls: Mapped[str | None] = mapped_column(LongText, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 思考过程单独存，不混进 content —— 回放时可以不展示
    reasoning: Mapped[str | None] = mapped_column(LongText, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")

    __table_args__ = (
        # 查"某会话的消息"永远按 id 升序取，这个复合索引正好覆盖
        Index("ix_messages_session_id_id", "session_id", "id"),
    )
