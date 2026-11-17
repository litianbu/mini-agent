"""和模型说话的这一层。

这一层只做一件事：把「一次模型调用」变成「一串增量」。
它不知道有 HTTP、不知道有浏览器、也不知道有 agent ——
只知道给我 messages，我给你 delta。

分工（每个文件一件事）：
    sse.py    行 → 帧          （最易错的协议细节）
    delta.py  增量的形状        （这一层的"货币"）
    mock.py   假模型            （离线替身）
    client.py 就是本文件：真调用 + 分发 + 错误翻译
"""

from __future__ import annotations

import json
import ssl
from collections.abc import AsyncIterator

import httpx

from ...core.config import settings
from ..delta import Delta, ToolCallDelta, Usage
from ..tools import schemas
from .mock import mock_deltas
from .sse import iter_frames

__all__ = ["Delta", "stream_deltas", "explain"]


async def stream_deltas(messages: list[dict]) -> AsyncIterator[Delta]:
    """唯一的对外入口。有 key 走真模型，没 key 走假模型。"""
    if settings.is_mock:
        async for delta in mock_deltas(messages):
            yield delta
    else:
        async for delta in real_deltas(messages):
            print('delta====', delta)
            yield delta


async def real_deltas(messages: list[dict]) -> AsyncIterator[Delta]:
    """真模型：POST + SSE，边收边吐。"""
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    payload: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": True,
        # 让最后一帧带上 token 用量（不加的话 usage 永远是 null）
        "stream_options": {"include_usage": True},
        # S3：把工具清单告诉模型。它据此决定要不要调用、调哪个。
        "tools": schemas(),
        "tool_choice": "auto",
    }
    # DeepSeek 的思考模式是个非标准扩展，只有它的接口认这个字段
    if settings.thinking and "deepseek" in settings.llm_base_url:
        payload["thinking"] = {"type": "enabled"}

    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    # read=None 表示"生成多久都等"；connect 必须有超时，否则连不上会卡死
    timeout = httpx.Timeout(connect=15.0, read=None, write=30.0, pool=15.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"模型返回 HTTP {resp.status_code}：{body[:300]}")

            async for raw in iter_frames(resp.aiter_lines()):
                if raw == "[DONE]":
                    return
                for delta in parse_chunk(raw):
                    yield delta


def parse_chunk(raw: str) -> list[Delta]:
    """一帧 JSON → 若干 Delta。字段缺失是常态，一律用 .get。"""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []  # 心跳之类的杂帧，丢掉

    # 先取 usage：它可能出现在任何一帧上，跟 choices 有没有内容无关。
    #
    # ⚠️ 实测这个网关的收尾帧长这样：
    #     {"choices":[{"index":0,"delta":{}}],"usage":{...}}
    #     choices 里有一个**空的** delta —— 所以不能靠"choices 为空"来识别它。
    #     一开始我按 OpenAI 文档写成 if not choices，结果 usage 永远是 0。
    u = obj.get("usage")
    usage = (
        Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
        )
        if u
        else None
    )

    choices = obj.get("choices") or []
    if not choices:
        # 只有 usage、没有正文的帧：单独作为一条 delta 吐出去
        return [Delta(usage=usage)] if usage else []

    choice = choices[0]
    delta = choice.get("delta") or {}

    reasoning = delta.get("reasoning_content") or ""
    text = delta.get("content") or ""
    finish = choice.get("finish_reason")

    # S3：工具调用碎片。注意这里是**数组**，模型可以同时调多个工具，
    # 每个碎片带自己的 index —— 上层按 index 分组累积。
    calls = [
        ToolCallDelta(
            index=int(item.get("index", 0)),
            id=item.get("id") or "",
            name=(item.get("function") or {}).get("name") or "",
            arguments=(item.get("function") or {}).get("arguments") or "",
        )
        for item in (delta.get("tool_calls") or [])
    ]

    # 什么都没解析到、也没有 usage 的帧（心跳之类）就丢掉
    if not (reasoning or text or finish or calls or usage):
        return []

    return [
        Delta(
            reasoning=reasoning,
            text=text,
            tool_calls=calls,
            finish_reason=finish,
            usage=usage,
        )
    ]


def explain(exc: BaseException) -> str:
    """把各种底层异常翻成一句有用的人话。

    留在这里而不是塞进 API 层，是因为"连不上模型"是这一层的知识。
    """
    if isinstance(exc, httpx.ConnectError):
        return f"连不上 {settings.llm_base_url}（检查地址/网络，或服务没起来）"
    if isinstance(exc, httpx.ReadTimeout):
        return "等模型响应超时了"
    if isinstance(exc, httpx.HTTPError):
        return f"请求模型失败：{exc}"
    if isinstance(exc, ssl.SSLError):
        return "TLS 握手失败（证书或代理问题）"
    return str(exc) or exc.__class__.__name__
