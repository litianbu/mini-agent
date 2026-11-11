"""事件契约 —— 整个项目最重要的一个文件。

前后端只通过这 5 种事件通信。它同时是三样东西：
  1. 后端产出事件时的类型约束
  2. 自动生成的 API 文档（FastAPI 会把它们放进 /openapi.json）
  3. 前端 src/types.ts 手写镜像的出处（改这里就要改那里）

为什么要"事件"而不是直接返回一段文字？
  因为流的本质是"过程"，一段字符串丢掉了过程：
  什么时候开始、正在想还是在答、为什么结束 ——
  这些信息后面加工具调用时全都要用到。
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class StartEvent(BaseModel):
    """流开始。带上模型名，前端能显示"现在是谁在回答"。"""

    type: Literal["start"] = "start"
    model: str
    mock: bool


class TextEvent(BaseModel):
    """正文增量。delta 是"这一小段"，不是全文 —— 前端的活是把它拼起来。"""

    type: Literal["text"] = "text"
    delta: str


class ReasoningEvent(BaseModel):
    """思考过程增量。和正文分开，因为它该用灰色小字显示。"""

    type: Literal["reasoning"] = "reasoning"
    delta: str


class DoneEvent(BaseModel):
    """正常结束。带统计信息，方便你在界面上看到"这次花了多久"。"""

    type: Literal["done"] = "done"
    finish_reason: str | None = None
    ms: int = 0
    chars: int = 0


class ErrorEvent(BaseModel):
    """出错。注意：出错也是一个**事件**，不是抛异常。

    这样前端的处理逻辑只有一条路径 —— 读事件流，
    不用额外写一套 try/except 去和流式解析纠缠。
    """

    type: Literal["error"] = "error"
    message: str


AgentEvent = Annotated[
    Union[StartEvent, TextEvent, ReasoningEvent, DoneEvent, ErrorEvent],
    Field(discriminator="type"),
]


def to_sse(event: BaseModel) -> str:
    """事件 → 一个 SSE 帧。

    SSE 只有两个硬规则：
      1. 每行以 `data:` 开头
      2. 一个空行表示这一帧结束
    所以帧 = 一行 data + 一个换行 + 空行。
    """
    return f"data: {event.model_dump_json()}\n\n"