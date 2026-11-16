"""依赖注入（FastAPI 的 Depends）。

现在只有一个 get_db，但它是"每请求一个会话"的落点：
    路由函数写 db: AsyncSession = Depends(get_db)，
    FastAPI 负责开连接、交给函数、函数返回后关闭。

为什么要按请求开，而不是全局共用一个 session：
    AsyncSession 不是线程/协程安全的，两个请求同时用会互相干扰事务。
    按请求开 = 各用各的，请求结束就还回去。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import SessionLocal


async def get_db() -> AsyncIterator[AsyncSession]:
    if SessionLocal is None:
        # 数据库没配（比如只想跑 mock 模式）：让调用方自己决定怎么办。
        # 这里抛 500 而不是返回 None，是为了让问题在开发时就暴露，
        # 而不是等线上某个接口莫名其妙 404。
        raise RuntimeError("数据库未配置：检查 DB_PASSWORD 等环境变量")
    async with SessionLocal() as session:
        yield session
