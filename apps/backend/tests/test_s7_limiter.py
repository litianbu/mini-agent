"""会话级限流：同一个会话同时只能有一个请求在跑。

为什么要测：这个闸门一旦漏放（release 没执行），
那个会话就**永远**发不出消息了（每次都 429），
而且用户看不出来问题在哪 —— 只会觉得"这个东西坏了"。

所以要双向测：该拒绝的要拒绝，该放行的要放行。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.core.limiter import SessionLimiter


@pytest_asyncio.fixture(autouse=True)
async def _dispose_pool():
    """每个用例结束后释放连接池。

    每个 pytest 用例跑在新的事件循环里，而连接池是全局的 ——
    上一个循环创建的连接在下一个循环里用会炸 "Event loop is closed"。
    """
    yield
    from app.core import database

    if database.SessionLocal is not None:
        await database.engine.dispose()


def test_second_request_is_rejected() -> None:
    """同一会话第二次进入要被挡住。"""
    lim = SessionLimiter()

    assert lim.acquire(1) is True
    assert lim.acquire(1) is False   # 撞车


def test_different_sessions_do_not_block_each_other() -> None:
    """不同会话互不影响 —— 这是"按会话限流"的意义。"""
    lim = SessionLimiter()

    assert lim.acquire(1) is True
    assert lim.acquire(2) is True
    assert lim.active_count == 2


def test_release_allows_next_request() -> None:
    """放锁之后要能继续发。"""
    lim = SessionLimiter()

    lim.acquire(1)
    lim.release(1)
    assert lim.acquire(1) is True


def test_double_release_is_safe() -> None:
    """重复释放不能抛异常。

    生成器关闭、异常路径、正常结束都可能触发 release，
    多调一次是常态，不能因此 500。
    """
    lim = SessionLimiter()
    lim.acquire(1)
    lim.release(1)
    lim.release(1)          # 不该炸
    lim.release(999)        # 从没占过的也不该炸

    assert lim.active_count == 0


def test_release_unknown_session_keeps_others() -> None:
    """释放一个不存在的会话，不能误伤别人。"""
    lim = SessionLimiter()
    lim.acquire(1)
    lim.acquire(2)

    lim.release(999)

    assert lim.is_active(1)
    assert lim.is_active(2)


def test_is_active_reflects_state() -> None:
    lim = SessionLimiter()
    assert lim.is_active(7) is False
    lim.acquire(7)
    assert lim.is_active(7) is True
    lim.release(7)
    assert lim.is_active(7) is False


# ── 和路由的接线（真实 HTTP 层）────────────────────────


@pytest.mark.asyncio
async def test_route_returns_429_when_session_busy() -> None:
    """会话正在跑时，接口要回 429 而不是继续生成。

    这里直接占住锁再请求，不需要真的并发 ——
    验证的是"接线对不对"，不是并发时序。
    """
    import httpx

    from app.core.database import SessionLocal
    from app.core.limiter import session_limiter
    from app.main import app
    from app.services import session_service

    if SessionLocal is None:
        pytest.skip("数据库未配置")

    # 先建一个真实会话 —— 限流检查在"会话存在性"之后，
    # 用一个不存在的 id 只会拿到 404，测不到限流
    async with SessionLocal() as db:
        s = await session_service.create_session(db, "限流测试")
        sid = s.id

    assert session_limiter.acquire(sid) is True
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat", json={"question": "你好", "session_id": sid}
            )
        assert resp.status_code == 429
        assert "还在生成" in resp.json()["detail"]
    finally:
        session_limiter.release(sid)
        async with SessionLocal() as db:
            await session_service.delete_session(db, sid)


@pytest.mark.asyncio
async def test_route_releases_lock_after_stream() -> None:
    """跑完一轮后锁要自动放掉，否则下次就 429 了。

    这是最要紧的一条：漏放 = 会话永久卡死。
    """
    import httpx

    from app.main import app
    from app.core.limiter import session_limiter
    from app.core.config import settings

    if not settings.is_mock:
        # mock 模式下不需要真的模型，测试才有确定性
        pytest.skip("需要 MOCK=1 才能跑通完整流")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        resp = await client.post("/api/chat", json={"question": "你好"})
        assert resp.status_code == 200

        # 拿新会话的 id
        for line in resp.text.splitlines():
            if line.startswith("data: ") and '"start"' in line:
                import json

                sid = json.loads(line[6:])["session_id"]
                break
        else:
            pytest.fail("没收到 start 事件")

        # 关键：流结束后锁必须已经放掉
        assert not session_limiter.is_active(sid), "流结束后锁没放，这个会话会永久 429"

        # 再发一次应该能过
        resp2 = await client.post("/api/chat", json={"question": "再来一次", "session_id": sid})
        assert resp2.status_code == 200
