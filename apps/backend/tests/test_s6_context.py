"""上下文裁剪：把发出去的 messages 压进预算，同时不破坏结构。

这是"防崩"的护栏。它出错的方式比不做更糟 ——
裁出一个残缺的工具调用，上游直接 400，会话就废了。

所以重点测三件事：
  1. 超长历史真的能压进预算
  2. 系统提示词 / 第一条用户消息不会被裁掉
  3. tool_calls 和 tool 结果永远成对（这条最要命）
"""

from __future__ import annotations

from app.agent.context import split_units, trim_to_budget
from app.agent.tokens import estimate_messages_tokens, estimate_tokens

SYSTEM = {"role": "system", "content": "你是一个助手。" * 100}


def _round(i: int, tool_chars: int = 1500) -> list[dict]:
    """造一轮"模型调工具 → 工具返回"的记录。"""
    cid = f"call_{i}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": cid, "content": "x" * tool_chars},
    ]


def _long_convo(rounds: int = 30) -> list[dict]:
    """30 轮带工具的历史。

    每轮工具输出 1500 字符 ≈ 900 token，30 轮约 3 万 token，
    明显超过 16000 的默认预算 —— 这样才测得到裁剪。
    """
    msgs = [SYSTEM, {"role": "user", "content": "开始任务"}]
    for i in range(rounds):
        msgs += _round(i)
        msgs.append({"role": "assistant", "content": f"第 {i} 轮做完了"})
    return msgs


# ── 基本行为 ────────────────────────────────────────────


def test_under_budget_is_untouched() -> None:
    """没超预算就一条都不动 —— 不能无端丢历史。"""
    msgs = [SYSTEM, {"role": "user", "content": "你好"}]
    out, dropped = trim_to_budget(msgs, 16000)

    assert out == msgs
    assert dropped == 0


def test_long_history_is_trimmed_into_budget() -> None:
    """30 轮带工具的历史，必须压进预算。"""
    msgs = _long_convo(30)
    assert estimate_messages_tokens(msgs) > 16000  # 前提：确实超了

    out, dropped = trim_to_budget(msgs, 16000)

    assert estimate_messages_tokens(out) <= 16000
    assert dropped > 0
    assert len(out) < len(msgs)


def test_system_prompt_survives() -> None:
    """系统提示词没了，模型就不知道有哪些工具。"""
    out, _ = trim_to_budget(_long_convo(30), 16000)
    assert out[0] == SYSTEM


def test_first_user_message_survives() -> None:
    """第一条用户消息是原始任务，丢了后面全无意义。"""
    out, _ = trim_to_budget(_long_convo(30), 16000)
    assert out[1] == {"role": "user", "content": "开始任务"}


def test_keeps_the_newest_messages() -> None:
    """丢弃的是最老的，保留的是最新的。"""
    out, _ = trim_to_budget(_long_convo(30), 16000)
    # 最后一条一定在（那是模型马上要接着干的事）
    assert out[-1] == {"role": "assistant", "content": "第 29 轮做完了"}


# ── 最要命的一条：配对不能拆 ──────────────────────────────


def test_tool_pairs_stay_intact() -> None:
    """裁完后，每条 tool 消息都要有对应的 assistant 声明。

    拆开的话上游直接 400 —— 比超窗更糟，因为超窗至少能改预算。
    """
    out, _ = trim_to_budget(_long_convo(30), 16000)

    declared = {
        c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]
    }
    results = {m["tool_call_id"] for m in out if m.get("role") == "tool"}

    assert results <= declared, f"有孤儿工具结果: {results - declared}"
    assert declared == results, "声明了却没结果，同样不合法"


def test_pairing_survives_size_mismatch() -> None:
    """声明很小、结果很大时，裁剪边界最容易落在两者之间。

    上面那个 30 轮的测试其实"运气好"：声明和结果大小接近，
    边界正好落在两者之间。这里把结果放大 20 倍，
    逼着边界必须落在一条巨大的 tool 消息上 ——
    此时正确的实现要么整组留、要么整组丢，不能只留一半。
    """
    msgs = [SYSTEM, {"role": "user", "content": "开始任务"}]
    for i in range(10):
        # 声明很小（几十字符），结果巨大（上万字符）
        msgs += _round(i, tool_chars=20000)
        msgs.append({"role": "assistant", "content": f"第 {i} 轮"})

    out, dropped = trim_to_budget(msgs, 16000)

    declared = {c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]}
    results = {m["tool_call_id"] for m in out if m.get("role") == "tool"}

    assert results <= declared, f"孤儿结果: {results - declared}"
    assert declared == results, f"声明无结果: {declared - results}"
    assert dropped > 0


def test_no_orphan_when_declaration_is_the_big_one() -> None:
    """声明比结果大得多时，最容易裁出孤儿 —— 这是真正的风险点。

    典型场景：模型调 write_file，arguments 里是**一整个文件**（几万字符），
    而结果只有一句"写入成功"。

    从尾部往回装时：
        结果很小 → 装得下，留下
        声明很大 → 装不下，被丢
    → 边界正好把一对拆开，留下一个孤儿 tool 消息，上游 400。

    把声明放大到超过整个预算，正确实现必须整组丢弃。
    """
    msgs = [SYSTEM, {"role": "user", "content": "开始任务"}]
    for i in range(8):
        cid = f"big_{i}"
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        # 一整个文件塞在 arguments 里
                        "function": {"name": "write_file", "arguments": "x" * 40000},
                    }
                ],
            }
        )
        # 结果很小
        msgs.append({"role": "tool", "tool_call_id": cid, "content": "写入成功"})
        msgs.append({"role": "assistant", "content": f"第 {i} 个文件写完"})

    out, dropped = trim_to_budget(msgs, 16000)

    declared = {c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]}
    results = {m["tool_call_id"] for m in out if m.get("role") == "tool"}

    assert results <= declared, f"孤儿结果: {results - declared}"
    assert declared == results, f"声明无结果: {declared - results}"
    assert dropped > 0


def test_never_returns_orphan_tool_at_head() -> None:
    """裁剪边界不能落在"结果"上 —— 列表开头不能是 role=tool。

    这是最容易写错的地方：从尾部往前数，正好数到一条 tool 消息时停手。
    """
    # 挑一个会让边界落在工具消息上的小预算
    for budget in range(800, 3000, 137):
        out, _ = trim_to_budget(_long_convo(30), budget)
        # 跳过页首的两条固定消息，看正文开头
        body = out[2:]
        if body:
            assert body[0].get("role") != "tool", f"预算 {budget} 时开头是孤儿结果"


def test_tool_pair_is_atomic_unit() -> None:
    """一组"声明 + 结果"在裁剪里是不可分割的单元。"""
    units = split_units(_long_convo(3)[2:])  # 去掉 system 和首条 user

    # 每轮 = 声明+结果（一组）+ 一条助手正文（一组）
    assert len(units) == 6
    assert len(units[0]) == 2
    assert units[0][0]["role"] == "assistant"
    assert units[0][1]["role"] == "tool"


# ── 边界情况 ────────────────────────────────────────────


def test_budget_larger_than_content_changes_nothing() -> None:
    msgs = _long_convo(2)
    out, dropped = trim_to_budget(msgs, 10_000_000)
    assert out == msgs
    assert dropped == 0


def test_zero_or_negative_budget_is_a_noop() -> None:
    """配置错成 0 时不能把历史清空 —— 宁可不裁。"""
    msgs = _long_convo(5)
    out, dropped = trim_to_budget(msgs, 0)
    assert out == msgs
    assert dropped == 0


def test_tiny_budget_still_keeps_head_and_shrinks() -> None:
    """预算极小（比系统提示词还小）时，至少保留页首，且不崩。"""
    out, dropped = trim_to_budget(_long_convo(10), 50)

    assert out[0] == SYSTEM
    assert out[1]["role"] == "user"
    assert dropped > 0


def test_each_call_has_its_own_result() -> None:
    """一轮多个并行调用时，只保留整组，不会只留其中一个。"""
    msgs = [
        SYSTEM,
        {"role": "user", "content": "查两个地方"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "结果A" * 200},
        {"role": "tool", "tool_call_id": "b", "content": "结果B" * 200},
        {"role": "assistant", "content": "都查完了"},
    ]
    out, _ = trim_to_budget(msgs, 1000)

    ids = {c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]}
    tids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    assert tids <= ids


# ── 估算本身 ────────────────────────────────────────────


def test_estimate_is_in_the_right_ballpark() -> None:
    """570 个汉字实测 330 token。估算要对得上，且略偏保守。"""
    text = "上下文管理是让对话可以长期进行的关键。" * 30
    assert len(text) == 570

    est = estimate_tokens(text)
    assert 330 <= est <= 400


def test_estimate_counts_tool_arguments() -> None:
    """工具参数常常很长（整个文件内容），漏了会低估一大截。"""
    plain = [{"role": "assistant", "content": "好"}]
    with_args = [
        {
            "role": "assistant",
            "content": "好",
            "tool_calls": [
                {"function": {"name": "write_file", "arguments": "x" * 2000}}
            ],
        }
    ]
    assert estimate_messages_tokens(with_args) > estimate_messages_tokens(plain) + 1000
