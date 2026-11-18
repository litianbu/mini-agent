"""一个最小的 MCP 天气服务器（stdio 传输）。

**为什么自己写，不用现成的**：

  1. 现成的天气 MCP 服务要真实 API key，跑不通就没法学
  2. 装第三方 MCP server 等于 `npx -y` 下载并运行别人的代码，
     而且它有你的文件系统权限 —— 供应链风险是真实的
  3. **自己写一遍服务端，协议才真正学明白**：
     客户端那边看到的"响应"，在服务端长什么样、什么时候发、id 从哪来，
     光当客户端是看不清的

它就是一个普通程序：从 stdin 读一行 JSON，往 stdout 写一行 JSON。
没有 HTTP、没有框架、没有依赖。

单独运行（调试用）：
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -m app.agent.mcp.weather_server
"""

from __future__ import annotations

import json
import sys

# 假数据。真实项目这里会调天气 API，对学习协议来说没区别。
_WEATHER = {
    "北京": ("晴", 26, "西北风 3 级"),
    "上海": ("多云", 24, "东风 2 级"),
    "广州": ("雷阵雨", 29, "南风 4 级"),
    "深圳": ("阵雨", 28, "东南风 3 级"),
    "杭州": ("阴", 23, "微风"),
}

_TOOL = {
    "name": "get_weather",
    "description": "查询中国某个城市的当前天气。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名，例如：北京、上海、广州",
            }
        },
        "required": ["city"],
    },
}


def _now() -> str:
    """不用 datetime 是为了让输出稳定 —— 测试里会比对文本。"""
    return "当前"


def get_weather(args: dict) -> dict:
    city = str(args.get("city") or "").strip()
    if not city:
        # isError=True：协议层面成功，业务层面失败。
        # 这两个要分开，否则模型分不清"服务坏了"和"参数写错了"。
        return {
            "isError": True,
            "content": [{"type": "text", "text": "缺少参数 city"}],
        }

    hit = _WEATHER.get(city)
    if hit is None:
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": f"没有 {city} 的数据。已知城市：{'、'.join(_WEATHER)}",
                }
            ],
        }

    desc, temp, wind = hit
    return {
        "content": [
            {"type": "text", "text": f"{city}（{_now()}）：{desc}，{temp}°C，{wind}"}
        ]
    }


_HANDLERS = {"get_weather": get_weather}


def handle(msg: dict) -> dict | None:
    """一条请求 → 一条响应。返回 None 表示这是通知，不该回。"""
    method = msg.get("method")
    req_id = msg.get("id")

    if req_id is None:
        # 通知：没有 id，规范说不许回。回了会打乱客户端。
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "weather", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [_TOOL]}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        handler = _HANDLERS.get(name or "")
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"没有这个工具：{name}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": handler(params.get("arguments") or {}),
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"不支持的方法：{method}"},
    }


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue        # 脏数据跳过，不崩

        reply = handle(msg)
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()      # ← 关键：不 flush 的话客户端会一直等


if __name__ == "__main__":
    main()
