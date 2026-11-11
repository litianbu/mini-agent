"""迷你 agent 的后端核心。

分层和一个真实的 Python 服务一模一样：

    config.py  读环境变量
    events.py  对外的事件契约（Pydantic 模型）
    llm.py     调模型、解析流
    chat.py    把两者粘起来，产出事件流 —— 这是"agent 核心"
    api.py     HTTP 层（FastAPI），只负责把事件流变成 SSE
    cli.py     终端层，只负责把事件流打印出来

api.py 和 cli.py 都不含任何模型逻辑，这是刻意的：
换传输方式（HTTP/WebSocket/CLI）不用碰核心。
"""

__version__ = "0.1.0"