"""会话表：一次对话一条记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ChatSession(Base):
    """一个会话 = 侧栏里的一行。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 标题先用第一条提问的开头，用户可以改
    title: Mapped[str] = mapped_column(String(200), default="新会话")

    # server_default 让数据库自己填时间，应用代码不用管
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 删会话时连带删消息。字符串写类名，避免和 message.py 循环导入
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )
