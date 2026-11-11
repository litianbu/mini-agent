"""请求/响应模型。

注意和 agent/events.py 的区别：
    events.py   流式契约（SSE 里的 5 种事件），前后端都认，稳定
    schemas/    普通 HTTP 请求体，只在这一跳有效，随接口演进
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """POST /api/chat 的请求体。"""

    # 限制长度不是为了防黑客，是让 400 在调用模型之前就发生 ——
    # 别把超长文本先发给模型，再发现不对劲。
    question: str = Field(min_length=1, max_length=8000)