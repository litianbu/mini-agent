"""MCP 的报文格式。

MCP 就是 **JSON-RPC 2.0**，没什么新鲜的。三种报文：

    请求（带 id，要回话）
    {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}

    成功响应
    {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}

    失败响应
    {"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"..."}}

    通知（**没有 id**，不回话）
    {"jsonrpc":"2.0","method":"notifications/initialized"}

**stdio 传输的分帧规则**：一行一条 JSON，换行符结尾。
注意不是 LSP 那种 Content-Length 头 —— 那是最常见的误解。
所以读的时候 readline() 就够，不用自己数字节。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["encode", "decode", "is_response_to", "rpc_error"]

# 我们支持的协议版本。真实握手时服务器会回它支持的版本，
# 不一致就按它说的来（下面的 client 不校验版本，只要求能通）。
PROTOCOL_VERSION = "2024-11-05"


def encode(payload: dict[str, Any]) -> bytes:
    """一条报文 → 一行字节（带换行）。

    ensure_ascii=False：中文原样发出去，不转成 \\uXXXX。
    separators：去掉多余空格，省点带宽，也让测试里的字符串更稳定。
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def decode(line: bytes) -> dict[str, Any] | None:
    """一行字节 → 一条报文。

    解析失败返回 None 而不是抛异常：stdio 里可能混进
    服务器自己的日志输出（很多服务器会往 stdout 打日志，
    虽然规范说应该打 stderr）。一遇到脏数据就崩，对接会很难受。
    """
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def is_response_to(payload: dict[str, Any], request_id: int) -> bool:
    """这条是不是我要的那条响应。

    **为什么必须按 id 匹配**：一个连接上可能同时有多个请求在飞，
    收到的响应顺序不保证。不按 id 认，就会把 A 的结果当成 B 的。
    """
    return payload.get("id") == request_id and (
        "result" in payload or "error" in payload
    )


def rpc_error(payload: dict[str, Any]) -> str:
    """从错误响应里取一句人话。"""
    err = payload.get("error") or {}
    code = err.get("code")
    msg = err.get("message") or "未知错误"
    return f"MCP 错误（{code}）：{msg}"
