"""S1 的测试。

三个测试，定三件重要的事：
  1. SSE 帧格式被钉住了 —— 以后谁改坏了，测试立刻红
  2. agent 的核心行为（先 start、最后 done、字数对得上）可回归
  3. 接口本身能起得来（health + openapi）

跑：pnpm test（等价于 cd apps/backend && uv run pytest -q）
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.agent.events import DoneEvent, StartEvent, TextEvent, to_sse
from app.agent.loop import chat
from app.core.config import settings
from app.main import app


def test_sse_frame_format() -> None:
    """一帧必须是 `data: {...}` 加一个空行，这是 SSE 的硬规矩。"""
    frame = to_sse(TextEvent(delta="你好"))

    # 结尾一定是两个换行：一个结束 data 行，一个表示"帧结束"
    assert frame.endswith("\n\n")
    assert frame.startswith("data: ")

    payload = frame[len("data: ") :].strip()
    obj = json.loads(payload)
    assert obj["type"] == "text"
    assert obj["delta"] == "你好"


async def test_mock_chat_emits_expected_sequence(monkeypatch) -> None:
    """mock 模式下的事件顺序：start → reasoning/text … → done。"""
    # 强制走假模型，这样测试不依赖网络和 key
    monkeypatch.setattr(settings, "mock", True)

    events = [e async for e in chat([{"role": "user", "content": "测试"}])]

    assert isinstance(events[0], StartEvent)
    assert events[0].mock is True
    assert isinstance(events[-1], DoneEvent)

    # 中间必须有正文，且 done 报告的字数要和真正收到的对得上
    text_events = [e for e in events if isinstance(e, TextEvent)]
    assert text_events, "应该有正文事件"
    total = sum(len(e.delta) for e in text_events)
    assert events[-1].chars == total
    assert events[-1].finish_reason == "stop"


def test_health_and_openapi() -> None:
    """/api/health 要有响应，/openapi.json 要能自动生成。"""
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["ok"] is True
        assert "model" in body

        schema = client.get("/openapi.json").json()
        assert "/api/chat" in schema["paths"]
