"""S6-1 的测试：把 token 用量变成看得见的数字。

只测一件事 —— 服务端返回的 `usage` 有没有被正确解析出来。
以前它在 `parse_chunk` 里被丢掉了，等于模型告诉你花了多少钱，你说不要。
"""

from __future__ import annotations


def test_parse_usage_frame_with_empty_delta() -> None:
    """网关的收尾帧长这样：choices 里有个空 delta，usage 挂在外面。

    这是实测抓包出来的形状，跟 OpenAI 文档写的不一样 ——
    按文档写 `if not choices` 会漏掉它，usage 永远是 0。
    """
    from app.agent.llm.client import parse_chunk

    raw = (
        '{"choices":[{"index":0,"delta":{}}],'
        '"usage":{"prompt_tokens":42,"completion_tokens":7,"total_tokens":49}}'
    )
    deltas = parse_chunk(raw)

    assert len(deltas) == 1
    assert deltas[0].usage is not None
    assert deltas[0].usage.prompt_tokens == 42
    assert deltas[0].usage.completion_tokens == 7
    assert deltas[0].usage.total_tokens == 49


def test_parse_usage_frame_without_choices() -> None:
    """另一种可能的形状：choices 干脆是空的。也要能取到。"""
    from app.agent.llm.client import parse_chunk

    raw = '{"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}'
    deltas = parse_chunk(raw)

    assert len(deltas) == 1
    assert deltas[0].usage is not None
    assert deltas[0].usage.total_tokens == 11


def test_parse_plain_text_frame_has_no_usage() -> None:
    """普通正文帧不带 usage，别凭空造一个。"""
    from app.agent.llm.client import parse_chunk

    deltas = parse_chunk('{"choices":[{"delta":{"content":"你好"}}]}')

    assert deltas[0].text == "你好"
    assert deltas[0].usage is None


def test_parse_heartbeat_is_dropped() -> None:
    """没有内容也没有 usage 的帧要丢掉，否则上层会收到空事件。"""
    from app.agent.llm.client import parse_chunk

    assert parse_chunk('{"choices":[],"usage":null}') == []


def test_usage_accumulates_across_rounds() -> None:
    """一次提问可能调模型多次，用量要能累加。

    这是 loop.py 里的用法：usage_total = usage_total + delta.usage
    """
    from app.agent.delta import Usage

    total = Usage()
    total = total + Usage(prompt_tokens=1342, completion_tokens=75, total_tokens=1417)
    total = total + Usage(prompt_tokens=1748, completion_tokens=197, total_tokens=1945)

    assert total.prompt_tokens == 3090
    assert total.completion_tokens == 272
    assert total.total_tokens == 3362
