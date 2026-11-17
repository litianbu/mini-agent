"""超时：两处，管的是不同的事。

  1. llm_read_timeout —— 两帧之间的最大间隔（httpx 层）
  2. agent_timeout    —— 整件事的总时长（asyncio 层）

为什么两个都要：
  httpx 的 read 超时只能发现"模型不出字了"，
  但发现不了"模型一直在出字、只是出了 20 分钟"。
  反过来，总时长管不了"卡在某一帧上不动"这种更早发现的情况。
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.loop import chat
from app.core.config import settings


def test_read_timeout_is_not_infinite() -> None:
    """read 不能是 None。

    None 看起来"宽容"（生成多久都等），实际是危险的：
    网关半死不活时（TCP 连着、偶尔发心跳、就是不输出正文），请求会永远挂着。
    """
    assert settings.llm_read_timeout is not None
    assert settings.llm_read_timeout > 0


def test_read_timeout_is_not_too_aggressive() -> None:
    """也不能设太小。

    模型思考十几秒才开始输出是正常的，
    read 超时卡在 5 秒会把正常请求全砍断。
    """
    assert settings.llm_read_timeout >= 30


def test_agent_timeout_is_reasonable() -> None:
    """总时长要够跑完 8 轮，又不能长到"卡一天"。"""
    assert 60 <= settings.agent_timeout <= 1800


def test_httpx_timeout_uses_the_config() -> None:
    """配置要真的传到 httpx 里，而不是写死在代码里。"""
    import inspect

    from app.agent.llm import client

    src = inspect.getsource(client)
    assert "settings.llm_read_timeout" in src, "read 超时没接配置"
    assert "read=None" not in src, "还有 read=None 的旧写法"


# ── 总时长真的能切断卡住的请求 ─────────────────────────


@pytest.mark.asyncio
async def test_total_timeout_stops_a_hanging_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型永远不出字时，总时长要把请求切掉，并给出人能懂的提示。

    做法：把 stream_deltas 换成一个"永远不返回"的假生成器，
    再把总时长调到 0.2 秒 —— 不这样测，就只能等 300 秒。
    """

    async def never(*_args, **_kwargs):
        await asyncio.sleep(3600)
        yield  # pragma: no cover  永远不会到这

    monkeypatch.setattr("app.agent.loop.stream_deltas", never)
    monkeypatch.setattr(settings, "agent_timeout", 0.2)

    events = []
    async for ev in chat([{"role": "user", "content": "你好"}]):
        events.append(ev)

    errors = [e for e in events if e.type == "error"]
    assert errors, "超时后应该发一个 error 事件"
    assert "秒上限" in errors[0].message
    # 提示里要告诉用户怎么办，而不是只报错
    assert "AGENT_TIMEOUT" in errors[0].message


@pytest.mark.asyncio
async def test_timeout_does_not_produce_done_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时是失败，不该同时发 done。

    否则前端会以为回答正常结束，把半截内容当成完整答案。
    """

    async def never(*_args, **_kwargs):
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    monkeypatch.setattr("app.agent.loop.stream_deltas", never)
    monkeypatch.setattr(settings, "agent_timeout", 0.2)

    events = [ev async for ev in chat([{"role": "user", "content": "你好"}])]

    assert not [e for e in events if e.type == "done"]


@pytest.mark.asyncio
async def test_normal_run_still_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """加了超时不能把正常流程搞坏。"""
    from app.agent.delta import Delta

    async def quick(*_args, **_kwargs):
        yield Delta(text="你好")
        yield Delta(finish_reason="stop")

    monkeypatch.setattr("app.agent.loop.stream_deltas", quick)

    events = [ev async for ev in chat([{"role": "user", "content": "你好"}])]

    assert [e for e in events if e.type == "done"], "正常结束应该发 done"
    assert not [e for e in events if e.type == "error"]
