"""数据库连接：这里管"怎么连上"。

表和表的关系在 models/，这里只做连接相关的事：
    engine      连接池（整个进程只有一个）
    SessionLocal 会话工厂（每个请求开一个）
    get_db()    FastAPI 依赖，自动开/关
    create_tables() 建表

建表命令：
    cd apps/backend && uv run python -m app.core.database
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings
from ..models import Base

# 没配密码时保持 None —— 数据库功能整块跳过，agent 本身照常跑。
# 和 MOCK 模式同一个思路：默认能跑，想要完整功能再配。
engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None

if settings.db_enabled:
    engine = create_async_engine(
        settings.db_url,
        echo=settings.db_echo,
        # 从池里取连接前先 ping。MySQL 会悄悄断开闲置连接（wait_timeout，
        # 默认 8 小时），不 ping 的话首次查询就报 "MySQL server has gone away"。
        pool_pre_ping=True,
        # 连接最多复用 1 小时，比服务端的 wait_timeout 短，主动换新的。
        pool_recycle=3600,
    )
    SessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        # commit 之后还能读对象属性。默认 True 会让属性失效，
        # 下次访问触发新查询 —— 在 async 里会报 MissingGreenlet，是个经典坑。
        expire_on_commit=False,
    )


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：给每个请求一个会话，用完自动关。

        @router.get("/sessions")
        async def list_sessions(db: AsyncSession = Depends(get_db)):
            ...

    没配数据库时直接抛错 —— 需要数据库的接口就该明确失败，
    而不是悄悄返回空列表。
    """
    if SessionLocal is None:
        raise RuntimeError("未配置 DB_PASSWORD（或 DATABASE_URL），数据库不可用")

    async with SessionLocal() as session:
        yield session


async def dispose() -> None:
    """关闭连接池。应用退出时调用，否则测试里会残留连接。"""
    if engine is not None:
        await engine.dispose()


async def create_tables() -> None:
    """建表：只创建**不存在**的表，已有的不动。

    ⚠️ 这不是迁移工具。给表加一列，再跑一次，这一列不会出现。
    表结构一旦要改，就得让 alembic 接手（S5 会加）。
    """
    if engine is None:
        print("✗ 未配置数据库密码，跳过建表（在 .env 里设置 DB_PASSWORD）")
        return

    async with engine.begin() as conn:
        # 先确认连的是哪个库 —— 建错库比建失败更麻烦
        db_name = (await conn.execute(text("SELECT DATABASE()"))).scalar()
        print(f"→ 目标库: {db_name}")

        await conn.run_sync(Base.metadata.create_all)
        rows = (await conn.execute(text("SHOW TABLES"))).scalars().all()
        print(f"✓ 表: {', '.join(rows) if rows else '(空)'}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create_tables())