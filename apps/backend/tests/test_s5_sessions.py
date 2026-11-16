"""S5 的测试：会话存库。

这些测试要连真实数据库（不是 mock）——
"存进去能不能读回来"这件事，用假数据库测等于没测。

所以每个测试都在自己建的会话上操作，结束就删掉，不污染开发数据。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.services import session_service


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """拿一个真实连接。数据库没配就跳过，而不是让整个测试套件红掉。

    为什么结束时必须 dispose 连接池：
        pytest-asyncio 给**每个测试**建一个新的事件循环，但连接池是模块级的、
        跨测试复用。上一个测试循环关闭后，池里那些连接的 transport 还挂着，
        下一个测试再用就会炸 "Event loop is closed"。
        dispose 把池子清空，下一个测试重新建连接。
    """
    if database.SessionLocal is None:
        pytest.skip("数据库未配置")
    async with database.SessionLocal() as session:
        yield session
    await database.engine.dispose()


async def test_create_and_read_back(db: AsyncSession) -> None:
    """存进去的会话能按 id 读回来，标题也在。"""
    s = await session_service.create_session(db, "测试会话标题")
    try:
        got = await session_service.get_session(db, s.id)
        assert got is not None
        assert got.title == "测试会话标题"
    finally:
        await session_service.delete_session(db, s.id)


async def test_empty_title_falls_back(db: AsyncSession) -> None:
    """空标题不能存成空串，否则侧栏会出现一行空白。"""
    s = await session_service.create_session(db, "   ")
    try:
        assert s.title == "新会话"
    finally:
        await session_service.delete_session(db, s.id)


async def test_messages_keep_order_and_count(db: AsyncSession) -> None:
    """消息按插入顺序读回，条数对得上 —— 这是"续聊"能工作的前提。"""
    s = await session_service.create_session(db, "顺序测试")
    try:
        await session_service.add_message(db, s.id, "user", "第一个问题")
        await session_service.add_message(db, s.id, "assistant", "第一个回答")
        await session_service.add_message(db, s.id, "user", "第二个问题")

        msgs = await session_service.list_messages(db, s.id)
        assert [m.role for m in msgs] == ["user", "assistant", "user"]
        assert msgs[0].content == "第一个问题"
        assert msgs[2].content == "第二个问题"
    finally:
        await session_service.delete_session(db, s.id)


async def test_messages_for_llm_shape(db: AsyncSession) -> None:
    """转给模型的格式必须是 [{role, content}]，且不含空内容。

    空 content 会被上游拒掉（"content must not be empty"），
    所以这个过滤不能少。
    """
    s = await session_service.create_session(db, "格式测试")
    try:
        await session_service.add_message(db, s.id, "user", "问题")
        await session_service.add_message(db, s.id, "assistant", "")  # 空回答
        await session_service.add_message(db, s.id, "assistant", "有内容")

        convo = await session_service.messages_for_llm(db, s.id)
        assert convo == [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "有内容"},
        ]
    finally:
        await session_service.delete_session(db, s.id)


async def test_delete_cascades_messages(db: AsyncSession) -> None:
    """删会话必须连消息一起删，不能留孤儿。

    这靠的是外键上的 ON DELETE CASCADE。
    没有它的话，删完会话 messages 表里会留一堆没人认领的行，
    越积越多，而且查不出来。
    """
    s = await session_service.create_session(db, "级联删除测试")
    sid = s.id
    await session_service.add_message(db, sid, "user", "会被一起删掉")

    assert await session_service.delete_session(db, sid) is True

    # 会话没了，消息也该没了
    assert await session_service.get_session(db, sid) is None
    assert await session_service.list_messages(db, sid) == []


async def test_delete_missing_returns_false(db: AsyncSession) -> None:
    """删不存在的会话要返回 False，路由据此给 404。"""
    assert await session_service.delete_session(db, 999_999) is False
