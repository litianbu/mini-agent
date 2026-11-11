"""数据模型：这里就是"表长什么样"。

一个文件放完所有表，打开就能看明白：
    Base         所有表的父类
    ChatSession  会话表
    Message      消息表（属于某个会话）

想加一张表，就在下面加一个 class，然后跑：
    cd apps/backend && uv run python -m app.core.database

为什么目录叫 models 不叫 db：
    "db" 太笼统 —— 看名字不知道里面是表结构还是连接代码。
    "models" 是这个概念的标准叫法（Django / Rails / FastAPI 教程都这么叫），
    看到目录名就知道"表定义在这里"。连接代码属于基础设施，归 core/。

以后表多了（比如 S9 要存工具调用），可以拆成
models/session.py、models/message.py 再在这里 re-export。
现在两张表，放一个文件反而更好读。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有表的基类。SQLAlchemy 靠它收集表结构（alembic 迁移也用它）。"""


# MySQL 的 TEXT 最多 64KB，长回答/长文件内容会爆。
# LONGTEXT 上限 4GB；其他数据库（如 SQLite）自动退回普通 Text。
LongText = Text().with_variant(LONGTEXT, "mysql")


def _new_id() -> str:
    """会话 id 用 UUID。

    不用自增整数：会话 id 会出现在 URL（/sessions/:id），
    自增值等于把"你有多少会话"告诉外面，也容易被挨个试。
    """
    return str(uuid.uuid4())


class ChatSession(Base):
    """一段聊天会话。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    model: Mapped[str] = mapped_column(String(100), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # cascade="all, delete-orphan"：删会话时消息一起删，
    # 否则会留下永远查不到的孤儿行。
    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """一条消息。role 决定它的含义。

    role 取值（和 OpenAI 约定一致，所以能直接塞进请求）：
        user       用户说的
        assistant  模型说的
        tool       工具返回的结果（S3 之后才会出现）
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(LongText, default="")
    # 思考过程（DeepSeek 的 reasoning_content），可以为空。
    # 存下来是为了回放会话；但不发给模型（浪费 token）—— 到 S5 会用到这个区别。
    reasoning: Mapped[str | None] = mapped_column(LongText, nullable=True)
    # S3 之后：工具调用与结果的 JSON。先留着，现在写 None。
    tool_calls: Mapped[str | None] = mapped_column(LongText, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    # 复合索引：最常见的查询是"某会话的消息按顺序"。
    __table_args__ = (Index("ix_messages_session_id_id", "session_id", "id"),)


__all__ = ["Base", "ChatSession", "Message"]
