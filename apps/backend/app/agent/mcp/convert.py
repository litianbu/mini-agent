"""把 MCP 的工具定义转成模型认识的形状。

两个生态说的是同一件事，但字段名不一样：

    MCP（Anthropic 那套）            OpenAI function calling
    ─────────────────────            ───────────────────────
    {"name": "get_weather",          {"type": "function",
     "description": "...",            "function": {
     "inputSchema": {                   "name": "get_weather",
        "type": "object",               "description": "...",
        "properties": {...},            "parameters": {
        "required": [...]                 "type": "object",
     }}                                   "properties": {...},
                                          "required": [...]
                                       }}}

**为什么要把名字改成 `mcp__server__工具名`**：

  1. 防撞车。MCP 服务器可能也提供一个叫 `bash` 或 `read_file` 的工具，
     直接混进 ALL_TOOLS 会覆盖掉本地的那个 —— 而且是静默覆盖，很难发现。
  2. 路由变简单。`execute()` 看到 `mcp__` 开头就知道该转发，
     不用维护"哪些名字属于哪个服务器"的映射表。
  3. 调试时一眼看出这个工具从哪来。

这也是 Claude Code 等工具的做法（它们用 `mcp__server__tool`）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["PREFIX", "qualified_name", "parse_name", "to_openai_schema"]

# 命名空间前缀。改这个要同时改 tools/__init__.py 里的路由判断。
PREFIX = "mcp__"

_SEP = "__"


def qualified_name(server: str, tool: str) -> str:
    """生成给模型看的工具名：weather + get_weather → mcp__weather__get_weather"""
    return f"{PREFIX}{server}{_SEP}{tool}"


def parse_name(name: str) -> tuple[str, str] | None:
    """把名字拆回 (服务器, 工具)。不是 MCP 工具就返回 None。

    注意用 split("__", 1) 而不是 split("__")：
    工具名本身可能含下划线，多切一刀就拆错了。
    """
    if not name.startswith(PREFIX):
        return None

    rest = name[len(PREFIX):]
    if _SEP not in rest:
        return None

    server, tool = rest.split(_SEP, 1)
    if not server or not tool:
        return None
    return server, tool


def to_openai_schema(server: str, tool: dict[str, Any]) -> dict[str, Any] | None:
    """一个 MCP 工具 → 一个 OpenAI 工具。数据不合法就返回 None（跳过）。

    这里**不做严格校验**：MCP 服务器的实现质量参差不齐，
    有的 list_tools 会漏字段。与其整批失败，不如跳过坏的那条 ——
    少一个工具，比让整个对话发不出去好。
    """
    raw_name = tool.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None

    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        # 没有参数定义的工具也是合法的，补一个空对象
        schema = {"type": "object", "properties": {}}

    return {
        "type": "function",
        "function": {
            "name": qualified_name(server, raw_name),
            # description 可能缺失。给一句兜底，否则模型完全不知道这工具干嘛的
            "description": tool.get("description") or f"（{server} 提供的工具）",
            "parameters": schema,
        },
    }
