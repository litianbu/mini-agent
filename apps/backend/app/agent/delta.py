"""一个增量（delta）。

单独一个文件，只为了打破循环依赖：
    client.py 要调用 mock.py，mock.py 要产出 Delta ——
    如果 Delta 定义在 client.py 里，两个文件就互相 import 了。

30 行也值得单独放，因为它是这一层的"货币"。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ToolCallDelta:
    """工具调用的一个碎片。

    ⚠️ S3 最容易踩的坑就在这里：
    模型不会一次把工具调用给你，而是拆成多帧：

        帧1: {"index":0,"id":"call_abc","function":{"name":"read_file","arguments":""}}
        帧2: {"index":0,"function":{"arguments":"{\"path\":"}}
        帧3: {"index":0,"function":{"arguments":"\"app/main.py\"}"}}

    所以 id / name 通常只在第一帧出现，arguments 则要**字符串拼接**。
    拼完才是合法 JSON。按 index 分组，因为模型可能同时调多个工具。
    """

    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class Delta:
    """模型吐出的一小块。

    四样东西都可能为空，也可能同时有：
      - reasoning：思考过程（DeepSeek 的 reasoning_content）
      - text：正文
      - tool_calls：工具调用碎片（S3 新增）
      - finish_reason：结束原因，只有最后一帧有
    """

    reasoning: str = ""
    text: str = ""
    tool_calls: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str | None = None


__all__ = ["Delta", "ToolCallDelta"]
