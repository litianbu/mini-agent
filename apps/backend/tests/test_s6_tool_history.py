"""工具调用落库：存进去的能原样还原回来。

S5 时工具调用没落库，`messages_for_llm` 只取 user/assistant 正文。
结果是"续聊"永远缺一块 —— 模型不记得之前查过什么。

补上之后最危险的地方不是"存不进去"，而是**配对被破坏**：
assistant 的 tool_calls 和随后的 role=tool 必须成对，少一半上游就 400，
而且一旦坏数据进了库，整个会话每次都 400，再也救不回来。

所以这里的重点是"坏数据要被挡掉"。
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.services import session_service


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    if database.SessionLocal is None:
        pytest.skip("数据库未配置")
    async with database.SessionLocal() as session:
        yield session
    # 每个测试用新事件循环，池子不清理会炸 "Event loop is closed"
    await database.engine.dispose()


def _calls(*ids: str) -> str:
    """造一段 tool_calls JSON，形状和 loop.py 里一致。"""
    return json.dumps(
        [
            {
                "id": i,
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
            }
            for i in ids
        ]
    )


async def test_tool_call_round_trip(db: AsyncSession) -> None:
    """完整的一轮：user → assistant(声明调用) → tool(结果) → assistant(回答)。

    还原后四条的 role 和字段都要对得上，顺序不能变。
    """
    s = await session_service.create_session(db, "工具回放测试")
    try:
        await session_service.add_message(db, s.id, "user", "数一下文件")
        await session_service.add_message(
            db, s.id, "assistant", "", tool_calls=_calls("call_a")
        )
        await session_service.add_message(
            db, s.id, "tool", "退出码 0\n5", tool_call_id="call_a"
        )
        await session_service.add_message(db, s.id, "assistant", "共 5 个")

        convo = await session_service.messages_for_llm(db, s.id)

        assert [m["role"] for m in convo] == ["user", "assistant", "tool", "assistant"]

        # assistant 那条必须带着 tool_calls，且 content 是空串（不是缺字段）
        assert convo[1]["tool_calls"][0]["id"] == "call_a"
        assert convo[1]["content"] == ""

        # tool 那条靠 tool_call_id 配对
        assert convo[2]["tool_call_id"] == "call_a"
        assert convo[2]["content"] == "退出码 0\n5"

        assert convo[3]["content"] == "共 5 个"
    finally:
        await session_service.delete_session(db, s.id)


async def test_orphan_tool_message_is_dropped(db: AsyncSession) -> None:
    """没有对应声明的 tool 消息必须丢掉。

    这是最要紧的一条：库里如果出现孤儿 tool 消息（比如写到一半崩溃），
    原样发出去上游会 400，而且这个会话以后每次都 400 —— 永久废掉。
    """
    s = await session_service.create_session(db, "孤儿测试")
    try:
        await session_service.add_message(db, s.id, "user", "问题")
        # 只有结果，没有 assistant 声明 —— 半截数据
        await session_service.add_message(
            db, s.id, "tool", "孤儿输出", tool_call_id="call_nowhere"
        )
        await session_service.add_message(db, s.id, "assistant", "回答")

        convo = await session_service.messages_for_llm(db, s.id)

        assert [m["role"] for m in convo] == ["user", "assistant"]
        assert all(m["role"] != "tool" for m in convo)
    finally:
        await session_service.delete_session(db, s.id)


async def test_multiple_calls_in_one_assistant(db: AsyncSession) -> None:
    """模型可以并行调多个工具：一条 assistant 声明两个，跟着两条 tool。"""
    s = await session_service.create_session(db, "并行调用测试")
    try:
        await session_service.add_message(db, s.id, "user", "看看两个文件")
        await session_service.add_message(
            db, s.id, "assistant", "", tool_calls=_calls("call_a", "call_b")
        )
        await session_service.add_message(db, s.id, "tool", "A 的内容", tool_call_id="call_a")
        await session_service.add_message(db, s.id, "tool", "B 的内容", tool_call_id="call_b")
        await session_service.add_message(db, s.id, "assistant", "两个都看了")

        convo = await session_service.messages_for_llm(db, s.id)

        assert len(convo[1]["tool_calls"]) == 2
        assert [m["role"] for m in convo] == ["user", "assistant", "tool", "tool", "assistant"]
        assert convo[2]["tool_call_id"] == "call_a"
        assert convo[3]["tool_call_id"] == "call_b"
    finally:
        await session_service.delete_session(db, s.id)


async def test_corrupt_tool_calls_json_does_not_break_session(db: AsyncSession) -> None:
    """tool_calls 是坏 JSON 时，跳过那条，而不是让整个会话发不出去。"""
    s = await session_service.create_session(db, "坏数据测试")
    try:
        await session_service.add_message(db, s.id, "user", "问题")
        await session_service.add_message(
            db, s.id, "assistant", "", tool_calls="{这不是合法 JSON"
        )
        await session_service.add_message(db, s.id, "assistant", "正常回答")

        convo = await session_service.messages_for_llm(db, s.id)

        # 坏的那条被丢弃，其它照常
        assert [m["role"] for m in convo] == ["user", "assistant"]
        assert convo[1]["content"] == "正常回答"
    finally:
        await session_service.delete_session(db, s.id)


async def test_empty_assistant_without_tools_is_skipped(db: AsyncSession) -> None:
    """空回答（没内容也没工具）不该出现在历史里，上游会拒绝空 content。"""
    s = await session_service.create_session(db, "空消息测试")
    try:
        await session_service.add_message(db, s.id, "user", "问题")
        await session_service.add_message(db, s.id, "assistant", "   ")
        await session_service.add_message(db, s.id, "assistant", "有内容")

        convo = await session_service.messages_for_llm(db, s.id)

        assert [m["content"] for m in convo] == ["问题", "有内容"]
    finally:
        await session_service.delete_session(db, s.id)
