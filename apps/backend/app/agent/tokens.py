"""估算一段文本大概占多少 token。

**为什么需要估算**：裁剪发生在请求发出**之前**，那时还拿不到模型的
`usage`（那是响应里才有的）。所以只能自己估。

**为什么不装 tiktoken**：那是 GPT 的分词器，跟当前模型的分词规则不同，
算出来一样不准，还多几百 KB 依赖。

**0.6 这个系数是量出来的**：拿 570 个汉字去问模型，`usage` 报 330 token，
即 1 token ≈ 1.73 汉字。倒数 0.578，取 0.6 **故意往高估** ——
宁可少装一点，也别算少了把窗口撑爆。
"""

from __future__ import annotations

import math

# 每个字符算多少 token。实测 0.578，取 0.6 偏保守。
TOKENS_PER_CHAR = 0.6

# 每条消息的固定开销：role 字段、分隔符、格式符。
# 不加上它，一屏很多条短消息时会低估。
TOKENS_PER_MESSAGE = 4


def estimate_tokens(text: str) -> int:
    """一段文本 ≈ 多少 token。"""
    if not text:
        return 0
    return math.ceil(len(text) * TOKENS_PER_CHAR)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """一组 messages 总共 ≈ 多少 token。

    除了正文，还要算两处 —— 漏了会低估一大截：

      1. tool_calls 里的 arguments（可能是整个文件内容）
      2. 每条消息的固定开销
    """
    total = 0
    for msg in messages:
        total += TOKENS_PER_MESSAGE
        total += estimate_tokens(str(msg.get("content") or ""))

        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total += estimate_tokens(str(fn.get("name") or ""))
            total += estimate_tokens(str(fn.get("arguments") or ""))

    return total


__all__ = [
    "TOKENS_PER_CHAR",
    "TOKENS_PER_MESSAGE",
    "estimate_tokens",
    "estimate_messages_tokens",
]
