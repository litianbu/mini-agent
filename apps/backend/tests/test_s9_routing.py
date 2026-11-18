"""S9-1：模型路由。

只做一件事：**把"用哪个模型"从硬编码变成一个可配置的决定**。
这一步还没有子 agent（S9-3 才有），所以测的是路由本身的行为。
"""

from __future__ import annotations

import pytest

from app.agent.llm import routing
from app.agent.llm.client import build_payload
from app.agent.llm.routing import MAIN, SUB, resolve
from app.core.config import settings


# ── 角色 → 模型 ─────────────────────────────────────────


def test_main_uses_default_model() -> None:
    assert resolve(MAIN) == settings.llm_model


def test_no_argument_means_main() -> None:
    """不传角色时按主 agent 处理 —— 老调用点不用改。"""
    assert resolve() == settings.llm_model


def test_sub_falls_back_to_main_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配子模型时沿用主模型。

    这是默认情况：只有一套模型的人不该被迫配两个。
    """
    monkeypatch.setattr(settings, "llm_model", "big-model")
    monkeypatch.setattr(settings, "llm_model_sub", "")
    assert resolve(SUB) == "big-model"


def test_sub_uses_its_own_model_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_model", "big-model")
    monkeypatch.setattr(settings, "llm_model_sub", "cheap-model")
    assert resolve(SUB) == "cheap-model"
    # 主模型不受影响 —— 两个角色各用各的
    assert resolve(MAIN) == "big-model"


def test_whitespace_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """空白字符串当作没配。

    .env 里手滑打个空格很常见（`LLM_MODEL_SUB= `），
    不 strip 的话会拿一个空模型名去请求，报错还很难懂。
    """
    monkeypatch.setattr(settings, "llm_model", "big-model")
    monkeypatch.setattr(settings, "llm_model_sub", "   ")
    assert resolve(SUB) == "big-model"


def test_unknown_role_falls_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """拼错角色名不该让对话崩掉。

    role 是内部字符串，写错是代码 bug。
    但为了一个拼写错误让整个对话失败不值得 —— 退化成"没有路由"更好。
    """
    monkeypatch.setattr(settings, "llm_model", "big-model")
    assert resolve("sb") == "big-model"
    assert resolve("") == "big-model"


# ── 模型名真的进了请求体 ───────────────────────────────


def test_payload_carries_explicit_model() -> None:
    payload = build_payload([], model="cheap-model")
    assert payload["model"] == "cheap-model"


def test_payload_falls_back_to_default() -> None:
    payload = build_payload([])
    assert payload["model"] == settings.llm_model


def test_payload_keeps_streaming_and_tools() -> None:
    """换了模型不该丢掉别的字段 —— 这是最容易改坏的地方。"""
    tools = [{"type": "function", "function": {"name": "x"}}]
    payload = build_payload([{"role": "user", "content": "hi"}], model="m", tools=tools)

    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == tools
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_payload_without_tools_is_valid() -> None:
    """没有工具时给空列表，不能是 None —— 上游会拒绝 null。"""
    assert build_payload([])["tools"] == []


# ── 全链路：chat(role=...) 真的用了那个模型 ─────────────


@pytest.mark.asyncio
async def test_chat_reports_and_uses_the_same_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """报给前端的模型和真正用的模型必须是同一个值。

    这是这一步最容易埋的坑：如果 StartEvent 和 stream_deltas
    各自算一次，一旦哪边改了，界面就会显示错的模型，
    而且**不会报任何错** —— 只能靠人肉发现。

    这里用假模型替身介入中间，把两边的值都抓住比对。
    """
    from app.agent import loop as loop_mod

    # is_mock 是派生属性（mock or not api_key），没有 setter。
    # 要让它为假，就改它的输入 —— 直接设 mock=False 不够，
    # 没 key 的话它还是真。
    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-fake")
    monkeypatch.setattr(settings, "llm_model", "big-model")
    monkeypatch.setattr(settings, "llm_model_sub", "cheap-model")

    used: list[str | None] = []

    async def fake_stream(messages, *, model=None):
        used.append(model)
        return
        yield  # pragma: no cover  — 让它是个生成器

    monkeypatch.setattr(loop_mod, "stream_deltas", fake_stream)

    events = [e async for e in loop_mod.chat([{"role": "user", "content": "hi"}], role=SUB)]

    start = next(e for e in events if e.type == "start")
    assert start.model == "cheap-model"
    assert used == ["cheap-model"], "报的和用的不一致"


@pytest.mark.asyncio
async def test_mock_mode_reports_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 模式下界面显示 MOCK，不该显示真实模型名。"""
    from app.agent import loop as loop_mod

    monkeypatch.setattr(settings, "mock", True)

    events = [e async for e in loop_mod.chat([{"role": "user", "content": "hi"}])]
    start = next(e for e in events if e.type == "start")
    assert start.model == "mock"


# ── 常量本身 ───────────────────────────────────────────


def test_role_names_are_stable() -> None:
    """角色名是配置和代码之间的契约，别随手改。"""
    assert routing.MAIN == "main"
    assert routing.SUB == "sub"
