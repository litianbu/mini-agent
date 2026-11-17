"""同一个会话，同时只允许一个请求在跑。

**不这么做会出什么事**：

  1. 用户连点两次「发送」→ 两个请求同时读同一份历史、各自生成回答、
     各自往库里写 → 消息顺序会乱，侧栏排序会跳
  2. 一次 agent 请求内部最多调 8 次模型，连点 5 次 = 40 次调用，
     每次都带上万 token 的历史 —— 花钱会失控

**为什么按会话而不是按 IP 限流**：

  按 IP 是防外人刷；按会话是防"自己跟自己撞车"。
  agent 场景里后者更常见：一个人开两个标签页、或者手快连点。
  两者不冲突，这个模块只做后者。

**已知局限（重要）**：

  这个集合存在**进程内存**里。单进程跑没问题，
  多进程（uvicorn --workers 4）时每个 worker 各记一份，
  同一个会话仍可能被两个 worker 同时处理。

  要真正严谨，得换成 Redis（`SET NX EX`）或数据库行锁。
  学习项目先到这儿，注释写清楚比假装没这回事好。
"""

from __future__ import annotations

__all__ = ["SessionLimiter", "session_limiter"]


class SessionLimiter:
    """会话级的并发闸门。

    用法：
        if not limiter.acquire(sid):
            raise HTTPException(429, ...)
        try:
            ...
        finally:
            limiter.release(sid)      # ← 一定要放在 finally
    """

    def __init__(self) -> None:
        self._active: set[int] = set()

    def acquire(self, session_id: int) -> bool:
        """占位。已经在跑就返回 False（调用方据此回 429）。"""
        if session_id in self._active:
            return False
        self._active.add(session_id)
        return True

    def release(self, session_id: int) -> None:
        """释放。discard 而不是 remove —— 重复释放不应该抛异常。"""
        self._active.discard(session_id)

    def is_active(self, session_id: int) -> bool:
        return session_id in self._active

    @property
    def active_count(self) -> int:
        return len(self._active)


# 全局单例。放模块级而不是依赖注入，
# 是因为它必须跨请求共享 —— 每个请求一份就没意义了。
session_limiter = SessionLimiter()
