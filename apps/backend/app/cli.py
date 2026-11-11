"""终端入口：和 Web 用的是同一份事件流。

这是分层的价值所在 —— agent 核心完全不知道 HTTP 的存在，
所以换个出口（终端）不用改一行核心代码。

用法：
    uv run mini-agent "帮我看看 app/main.py 有多少行"
    没给参数时进交互模式，一行一个提问。
"""

from __future__ import annotations

import sys

from .agent.events import (
    DoneEvent,
    ErrorEvent,
    ReasoningEvent,
    StartEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .agent.loop import chat
from .core.config import settings


def _dim(text: str) -> str:
    """终端里显示成灰色（思考过程用）。"""
    return f"\033[2m{text}\033[0m"


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


async def _ask(question: str) -> int:
    """跑一次对话，把事件打印到终端。返回退出码。"""
    print(_dim(f"· {settings.llm_model}"), file=sys.stderr)

    code = 0
    try:
        async for event in chat([{"role": "user", "content": question}]):
            if isinstance(event, StartEvent):
                if event.mock:
                    print(_dim("· MOCK 模式（未配置 key，用本地假数据）"), file=sys.stderr)

            elif isinstance(event, ReasoningEvent):
                # 思考过程走 stderr：和正文分开，方便 `> answer.txt` 只留答案
                print(_dim(event.delta), end="", file=sys.stderr, flush=True)

            elif isinstance(event, TextEvent):
                print(event.delta, end="", flush=True)

            elif isinstance(event, ToolCallEvent):
                # 工具调用单独一行、带颜色，和正文区分开
                print(_cyan(f"\n  ⚙ {event.name}({event.arguments[:80]})"), file=sys.stderr)

            elif isinstance(event, ToolResultEvent):
                mark = "✓" if event.ok else "✗"
                head = event.output.splitlines()[0][:100] if event.output else ""
                color = _dim if event.ok else _red
                print(color(f"  {mark} {head}"), file=sys.stderr, flush=True)

            elif isinstance(event, DoneEvent):
                print()  # 正文结束后换行
                print(
                    _dim(
                        f"〔{event.finish_reason or '-'} · {event.chars} 字 · "
                        f"{event.ms}ms · {event.rounds} 轮〕"
                    ),
                    file=sys.stderr,
                )

            elif isinstance(event, ErrorEvent):
                print(f"\n[错误] {event.message}", file=sys.stderr)
                code = 1

    except KeyboardInterrupt:
        # Ctrl+C：中断生成。退出码 130 是"被 SIGINT 终止"的惯例值。
        print("\n[已取消]", file=sys.stderr)
        return 130

    return code


def main() -> None:
    args = sys.argv[1:]

    if args:
        raise SystemExit(_run(" ".join(args)))

    # 交互模式：一行一个问题，空行退出
    print(_dim("交互模式，回车发送，Ctrl+D 或空行退出"))
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        _run(line)


def _run(question: str) -> int:
    import asyncio

    return asyncio.run(_ask(question))


if __name__ == "__main__":
    main()
