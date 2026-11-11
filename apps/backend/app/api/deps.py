"""依赖注入（FastAPI 的 Depends）。

现在还没有依赖 —— S5 接数据库后会多出一个 get_db：

    def get_db() -> Iterator[Session]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

然后在路由里写 db: Session = Depends(get_db)，
FastAPI 会自动开/关连接，请求结束就回收。
"""
