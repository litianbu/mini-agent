"""一个增量（delta）。

单独一个文件，只为了打破循环依赖：
    llm.py 要调用 mock.py，mock.py 要产出 Delta ——
    如果 Delta 定义在 llm.py 里，两个文件就互相 import 了。

18 行也值得单独放，因为它是这一层的"货币"。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Delta:
    """模型吐出的一小块。

    三样东西都可能为空，也可能同时有：
      - reasoning：思考过程（DeepSeek 的 reasoning_content）
      - text：正文
      - finish_reason：结束原因，只有最后一帧有
    """

    reasoning: str = ""
    text: str = ""
    finish_reason: str | None = None