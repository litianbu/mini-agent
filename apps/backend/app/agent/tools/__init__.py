"""工具注册表：模型能用的所有工具都在这。

四个工具够用了：读、写、找、跑命令。
想做更多（比如 edit_file 精确替换、web 搜索），在这里加一个 Tool 即可 ——
loop.py 一行都不用改。
"""

from __future__ import annotations

import json
from typing import Any

from .base import Tool
from .edit import EDIT
from .fs import GLOB_SEARCH, READ_FILE, WRITE_FILE
from .search import GREP
from .shell import RUN_BASH

ALL_TOOLS: list[Tool] = [READ_FILE, WRITE_FILE, EDIT, GLOB_SEARCH, GREP, RUN_BASH]

_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def schemas() -> list[dict[str, Any]]:
    """给模型看的工具清单（放进请求的 tools 字段）。"""
    return [tool.schema() for tool in ALL_TOOLS]


async def execute(name: str, arguments: str) -> str:
    """执行一个工具调用。

    两个必须处理的意外（模型很常见）：
      1. 工具名不存在 —— 它可能记错名字
      2. 参数不是合法 JSON —— 拼参数时被截断
    这两种都不能让整个对话崩掉：把错误当成"工具结果"返回给模型，
    它看到错误信息往往会自己纠正，这比直接抛异常有用得多。
    """
    tool = _BY_NAME.get(name)
    if tool is None:
        return f"错误：没有名为 {name!r} 的工具。可用：{', '.join(_BY_NAME)}"

    try:
        args = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return f"错误：参数不是合法 JSON（{exc}）。收到的是：{arguments[:200]}"

    try:
        return await tool.run(args)
    except Exception as exc:
        return f"工具执行出错：{exc.__class__.__name__}: {exc}"


__all__ = ["ALL_TOOLS", "schemas", "execute"]
