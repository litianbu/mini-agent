"""数据库层的测试。

重点不是"能连上"（那依赖本机 MySQL，CI 上没有），
而是两件与机器无关的事：
    1. 没配数据库时，健康检查报 disabled 而不是崩
    2. 配了真库时，报 ok

真实连接测试连不上就 skip —— 测试应该在任何机器上都能跑。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import database
from app.core.config import settings
from app.main import app


def test_health_reports_db_disabled_without_url(monkeypatch) -> None:
    """没配库时，health 应该老实说 disabled。"""
    # 换掉模块级 engine —— health 里是运行时查 database.engine，所以有效
    monkeypatch.setattr(database, "engine", None)
    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is True
    assert body["db"] == "disabled"


@pytest.mark.skipif(not settings.db_enabled, reason="未配置 DB_PASSWORD")
def test_health_reports_db_ok_with_real_connection() -> None:
    """配了真库时应该报 ok。没有本机 MySQL 的机器会 skip 掉。"""
    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert body["db"] == "ok"


@pytest.mark.skipif(not settings.db_enabled, reason="未配置 DB_PASSWORD")
async def test_tables_exist_in_database() -> None:
    """两张表真的在库里 —— 防止"代码加了模型但忘了建表"。"""
    assert database.engine is not None
    async with database.engine.connect() as conn:
        rows = (await conn.execute(text("SHOW TABLES"))).scalars().all()

    assert {"sessions", "messages"} <= set(rows)
