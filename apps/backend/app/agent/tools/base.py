"""工具的统一定义。

一个工具 = 三样东西：
    名字        模型用这个名字调用
    描述        模型靠这段文字判断"什么时候该用"（写不好它就不用）
    参数 schema  JSON Schema，模型据此生成参数

加上一个真正干活的 run()。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]

    def schema(self) -> dict[str, Any]:
        """转成 OpenAI 的 tools 格式。

        这个格式不是我们定的，是模型接口定的 —— 三个字段名不能改。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
