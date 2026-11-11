"""应用入口：把各层组装起来。

这个文件应该"薄且无聊" —— 只有 create_app() 和挂载动作。
如果哪天它开始出现业务逻辑，说明分层被破坏了。

启动：
    uvicorn app.main:app --reload      （开发）
    python -m app.main                 （等价，见文件末尾）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import __version__
from .api.routes import chat, health
from .core import database
from .core.config import REPO_ROOT, settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用的生命周期钩子：启动时一次，退出时一次。

    启动时只检查连通性，不建表 —— 建表是
    `uv run python -m app.core.database` 的事。
    让服务在启动时改数据库结构，是很多事故的来源。

    数据库连不上也别让服务起不来：agent 的核心功能不依赖数据库，
    最多是会话存不下来。日志里喊一声，服务照常跑。
    """
    if database.engine is None:
        print("· 未配置数据库密码，数据库功能关闭")
    else:
        try:
            async with database.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            # 只打印 @ 之后的部分（主机/库名），别把密码写进日志
            print(f"· 数据库已连接: {settings.db_url.split('@')[-1]}")
        except Exception as exc:
            print(f"! 数据库连接失败（服务继续启动）: {exc}")

    yield

    # 退出时把连接池还回去，否则 reload 几次就会攒一堆僵尸连接
    await database.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="mini-agent",
        version=__version__,
        description="迷你编码 agent 的后端。POST /api/chat 返回 SSE 事件流。",
        lifespan=lifespan,
    )

    # ── 接口 ────────────────────────────────────────────
    # 统一 /api 前缀：以后加 v2 或者换网关，路径规则好迁移。
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(health.router, prefix="/api", tags=["health"])

    # ── 前端产物（生产模式）──────────────────────────────
    # 开发时用不上（前端由 Vite 服务，还带热更新）；
    # 但 pnpm build 之后，一个 uvicorn 进程就能同时提供页面和 API。
    #
    # check_dir=False 是有意的：没 build 过时 dist 不存在，
    # 这里不报错，只是访问页面会得到 503，/api 照常工作。
    web_dist = REPO_ROOT / "apps" / "frontend" / "dist"
    app.mount(
        "/assets",
        StaticFiles(directory=web_dist / "assets", check_dir=False),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """前端路由兜底。

        我们用的是 BrowserRouter（真实路径、没有 # 号），所以直接刷新
        /sessions 会打到后端 —— 后端并没有这个文件。这里统一返回
        index.html，交给前端路由自己决定渲染哪个页面。

        注意这条路由必须放在最后：它会兜住所有没匹配上的路径。
        """
        if full_path.startswith("api/"):
            # 未匹配的 API 路径应该老老实实报 404，而不是返回一个 HTML
            raise HTTPException(status_code=404, detail="接口不存在")

        index = web_dist / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="前端还没构建：先在仓库根目录跑 pnpm build",
            )
        return FileResponse(index)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=settings.port,
        reload=True,
    )
