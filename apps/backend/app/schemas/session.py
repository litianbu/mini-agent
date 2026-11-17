"""会话相关的 HTTP 契约。

注意这里没有直接复用 SQLAlchemy 模型：模型是"库里长什么样"，
schema 是"接口对外长什么样"。两者分开，改表结构才不会意外改坏接口。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionSummary(BaseModel):
    """侧栏一项：会话 + 消息条数。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    """一条消息。前端靠 role 决定画左边还是右边。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    reasoning: str | None = None

    # 工具调用。assistant 那条带 tool_calls（JSON 串），
    # 紧跟的 tool 那条带 tool_call_id —— 两者配对，缺一不可。
    tool_calls: str | None = None
    tool_call_id: str | None = None

    created_at: datetime


class SessionDetail(BaseModel):
    """打开某个会话时返回的完整内容。"""

    id: int
    title: str
    messages: list[MessageOut]
