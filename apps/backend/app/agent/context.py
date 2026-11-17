"""上下文裁剪：把发出去的 messages 压进预算。

**要解决的问题**：历史只增不减，某天超过模型窗口上限，
接口返回 400 —— 而且最糟的是这个会话**永久废掉**：
超长历史存在库里，下次打开还发一遍，还 400。

**为什么不做摘要**（把老对话压缩成一段话）：
那要额外调一次模型（多花钱、多一个故障点），
而且摘要会把"具体执行了什么命令"揉成一团模糊的描述。
确定性裁剪虽然粗暴，但行为可预测 —— 作为第一道护栏更合适。

**三条不可让步的规则**：

  1. 系统提示词必须留着（不然模型不知道有哪些工具）
  2. 第一条用户消息必须留着（那是原始任务）
  3. assistant 的 tool_calls 和配对的 tool 消息**不能拆开**
     （拆了上游直接 400，比超窗更糟）
"""

from __future__ import annotations

from .tokens import estimate_messages_tokens

__all__ = ["trim_to_budget", "split_units"]


def _is_tool_of(msg: dict, ids: set[str]) -> bool:
    """这条是不是属于 ids 里某次调用的结果。"""
    return msg.get("role") == "tool" and msg.get("tool_call_id") in ids


def split_units(messages: list[dict]) -> list[list[dict]]:
    """把消息切成"不可分割的单元"。

    绝大多数消息自己就是一个单元；唯独
    `assistant(带 tool_calls) + 紧随其后的 tool 结果`
    必须绑在一起 —— 裁剪时要么全留，要么全丢。
    """
    units: list[list[dict]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            ids = {c.get("id") for c in msg["tool_calls"] if c.get("id")}
            block = [msg]
            j = i + 1
            # 紧跟的结果都归这一组。id 对不上的结果属于别的调用，不能抢。
            while j < len(messages) and _is_tool_of(messages[j], ids):
                block.append(messages[j])
                j += 1
            units.append(block)
            i = j
        else:
            units.append([msg])
            i += 1

    return units


def trim_to_budget(messages: list[dict], budget: int) -> tuple[list[dict], int]:
    """从最新往回装，装到预算为止。

    返回 `(裁剪后的列表, 丢掉的消息条数)`。

    设计上刻意丢掉**最老的**那些：
    最近几轮才是模型当前在干的事。
    """
    if budget <= 0:
        return list(messages), 0

    if estimate_messages_tokens(messages) <= budget:
        return list(messages), 0

    # ① 固定页首：开头的 system 消息（可能有多条）
    head_end = 0
    while head_end < len(messages) and messages[head_end].get("role") == "system":
        head_end += 1
    head = messages[:head_end]
    rest = messages[head_end:]

    # ② 固定第一条用户消息（原始任务）。它可能就在 head 之后
    first_user: list[dict] = []
    if rest and rest[0].get("role") == "user":
        first_user = [rest[0]]
        rest = rest[1:]

    used = estimate_messages_tokens(head + first_user)

    # ③ 从尾部往前装整单元
    units = split_units(rest)
    keep: list[list[dict]] = []
    for unit in reversed(units):
        cost = estimate_messages_tokens(unit)
        if used + cost > budget:
            # 装不下了就停。不切半个单元 —— 半组工具调用发出去就 400
            break
        keep.append(unit)
        used += cost

    kept = [m for unit in reversed(keep) for m in unit]
    trimmed = head + first_user + kept

    # 兜底：如果连"页首 + 第一条用户消息"都超预算，
    # 说明单条消息本身已经过大（工具输出治理 L0 该管的事），
    # 这时仍然要发出去 —— 但至少保证形状合法。
    return trimmed, len(messages) - len(trimmed)
