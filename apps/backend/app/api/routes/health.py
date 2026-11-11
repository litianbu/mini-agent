"""健康检查：给前端和运维看的探针。

返回"服务活着吗、用什么模型、数据库通不通"。
这种接口看起来没用，但上线后第一个要看的就是它。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from ... import __version__
from ...core import database
from ...core.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "mock": settings.is_mock,
        "model": "mock" if settings.is_mock else settings.llm_model,
        "db": await _db_status(),
    }


async def _db_status() -> str:
    """数据库状态：disabled / ok / error。

    注意这里是主动查询而不是看"配置里有没有密码" ——
    配了连不上，比没配更值得报警。所以真跑一次 SELECT 1。

    另一个细节：这里用 `database.engine` 而不是
    `from ...core.database import engine`。
    后者是值拷贝，模块导入时就把当时的对象绑死了：
    之后谁把 engine 换成 None（测试、或将来支持热重载配置），
    这里还指着旧对象。运行时查模块属性才是对的。
    """
    engine = database.engine
    if engine is None:
        return "disabled"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"
