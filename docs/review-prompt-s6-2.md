# 任务：审查一段「LLM 上下文裁剪」实现，找出真实缺陷

你是一位严格的代码审查者。下面是一个小型 AI Agent 项目新增的上下文裁剪模块。
**请以对抗性视角审查：默认这里有 bug，你的任务是找出来，而不是确认它没问题。**

不要评价代码风格和注释。只关心「会不会出错」。

---

## 项目背景

- Python 3.12 + FastAPI + SQLAlchemy 2.x(async) + MySQL
- 手写 ReAct 循环 agent（不用 LangChain 等框架）
- 流程：用户提问 → 读 MySQL 历史 → 拼成 OpenAI messages → 调模型（流式）
  → 模型可能返回 tool_calls → 执行工具 → 结果追加回 messages → 再调模型，最多 8 轮
- 工具：bash / read_file / write_file / edit / grep / glob
- 消息形状（OpenAI 标准）：

```python
{"role": "system", "content": "..."}
{"role": "user", "content": "..."}
{"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
    "function": {"name": "bash", "arguments": "{...}"}}]}
{"role": "tool", "tool_call_id": "call_1", "content": "命令输出..."}
{"role": "assistant", "content": "最终回答"}
```

## 要解决的问题

历史只增不减。超过模型窗口上限后接口返回 400，而且**这个会话会永久废掉**
——超长历史存在库里，下次打开还发一遍，还 400。

所以新增裁剪：每次请求前把 messages 压进预算（默认 16000 token），
超出就从**最老的**开始丢。

## 必须成立的不变量（审查重点）

1. 开头 system 消息**必须保留**（没有它模型不知道有哪些工具）
2. 第一条用户消息**必须保留**（原始任务）
3. `assistant.tool_calls` 与配对的 `tool` 消息**不能拆开**（拆了上游 400）
4. 裁剪后总 token ≤ 预算（除非单条消息本身就超预算）
5. 相对顺序不变
6. 预算配成 0 或负数时不能清空历史（宁可不裁）

## 不接受质疑的设计约束

- token 是**估算**值，系数 0.6 token/字符 是实测校准后故意取高
- 不装 tiktoken（分词器与目标模型不匹配）
- 不用摘要压缩（要多调一次模型，行为不可预测）
- 裁剪在**每轮**模型调用前执行，不只是请求入口

---

## 代码

### 文件 1：`app/agent/context.py`（核心，新增）

```python
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
```

### 文件 2：`app/agent/tokens.py`（估算，新增）

```python
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
```

### 文件 3：`app/agent/loop.py`（ReAct 主循环，裁剪接入点）

```python
async def chat(
    messages: list[Message],
    *,
    thinking: bool | None = None,
) -> AsyncIterator[AgentEvent]:
    """一次对话 = 一串事件。

    调用方只需要写：
        async for event in chat(messages):
            ...

    它不关心底层是 SSE、是 WebSocket，还是别的什么。
    """
    started = time.perf_counter()
    total_chars = 0
    finish_reason: str | None = None
    rounds = 0

    # token 用量：一次提问可能调用模型多次（每轮工具调用一次），
    # 所以要累加，否则只能看到最后一轮的数字。
    usage_total = Usage()

    # 被裁掉多少条，用于观测（只在开发环境显示给开发者看）
    trimmed_total = 0

    # 复制一份：后面会往里追加 assistant / tool 消息。
    # 如果直接改调用方传进来的 list，多轮对话的历史会被污染。
    convo: list[Message] = list(messages)

    # 系统提示词只加一次。调用方已经带了就不重复插 ——
    # 多轮对话时 messages 里可能已经有历史，别把它挤掉。
    if not convo or convo[0].get("role") != "system":
        convo.insert(0, {"role": "system", "content": system_prompt()})

    yield StartEvent(
        model="mock" if settings.is_mock else settings.llm_model,
        mock=settings.is_mock,
    )

    try:
        while rounds < MAX_ROUNDS:
            rounds += 1
            acc = _Accumulator()
            round_text = ""

            # 每轮都裁，不是只在入口裁一次 ——
            # 工具输出会在一轮内不断累积，入口裁过之后还会涨回去。
            convo, dropped = trim_to_budget(convo, settings.context_budget)
            if dropped:
                trimmed_total += dropped
                # 裁剪会改缓存前缀，日志里留个痕迹，排查"为什么它忘了"时有用
                print(f"[context] 超出预算，裁掉 {dropped} 条")

            async for delta in stream_deltas(convo):
                if delta.reasoning:
                    yield ReasoningEvent(delta=delta.reasoning)

                if delta.text:
                    round_text += delta.text
                    total_chars += len(delta.text)
                    yield TextEvent(delta=delta.text)

                if delta.tool_calls:
                    acc.feed(delta.tool_calls)

                if delta.usage:
                    usage_total = usage_total + delta.usage

                if delta.finish_reason:
                    finish_reason = delta.finish_reason

            # ── 没有工具调用 → 这次回答就结束了 ──────────────
            if not acc:
                break

            calls = acc.result()

            # ① 先把"模型想做什么"记进对话历史。
            #    tool_calls 必须原样保留：下一轮请求里，
            #    模型要靠 tool_call_id 找到自己刚才的意图。
            convo.append(
                {
                    "role": "assistant",
                    "content": round_text,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]},
                        }
                        for c in calls
                    ],
                }
            )

            # ② 逐个执行，结果作为 role="tool" 的消息塞回去
            for call in calls:
                # 开始调用工具
                yield ToolCallEvent(
                    id=call["id"], name=call["name"], arguments=call["arguments"]
                )
                # 真正执行
                output = await execute(call["name"], call["arguments"])
                ok = not output.startswith(("错误：", "工具执行出错：", "拒绝执行："))
                # 执行完毕
                yield ToolResultEvent(
                    id=call["id"], name=call["name"], ok=ok, output=output
                )

                convo.append(
                    {
                        "role": "tool",
                        # 这个 id 就是配对的关键：模型据此知道
                        # 这份结果属于哪一次调用
                        "tool_call_id": call["id"],
                        "content": output,
                    }
                )
            # ③ 回到 while 顶部：带着工具结果再问模型一次

        else:
            # while 正常跑满 MAX_ROUNDS 才会走到这里（没 break）
            yield ErrorEvent(
                message=f"已连续调用工具 {MAX_ROUNDS} 轮仍未结束，强制停止。"
                "可能是任务太大，或是模型在原地打转。"
            )

    except asyncio.CancelledError:
        # 用户关了页面 / 点了停止。必须重新抛出，
        # 否则取消信号被吞掉，上层会以为任务正常结束了。
        raise
    except Exception as exc:
        yield ErrorEvent(message=explain(exc))
        return

    yield DoneEvent(
        trimmed=trimmed_total,
        prompt_tokens=usage_total.prompt_tokens,
        completion_tokens=usage_total.completion_tokens,
        total_tokens=usage_total.total_tokens,
        finish_reason=finish_reason,
        ms=int((time.perf_counter() - started) * 1000),
        chars=total_chars,
        rounds=rounds,
    )
```

### 文件 4：`app/services/session_service.py` 中的历史还原（裁剪的输入来源）

```python
async def messages_for_llm(db: AsyncSession, session_id: int) -> list[dict]:
    """把库里的消息还原成模型认识的形状（OpenAI messages）。

    S5 时这里只取 user/assistant 正文，工具调用被整个跳过 ——
    因为那时工具消息没落库，硬拼一个残缺的 tool 消息会让上游报错。
    现在工具调用落库了，可以完整还原。

    **配对是硬约束**：assistant 的 tool_calls 和随后的 role=tool
    必须成对出现。少一半，上游直接 400。所以下面用 known 集合把关：
    只有 id 确实被某个 assistant 声明过的 tool 消息才放行。
    """
    rows = await list_messages(db, session_id)

    out: list[dict] = []
    known: set[str] = set()  # 已经被声明过的工具调用 id

    for m in rows:
        if m.role == "user":
            if m.content:
                out.append({"role": "user", "content": m.content})

        elif m.role == "assistant":
            item: dict = {"role": "assistant", "content": m.content or ""}

            if m.tool_calls:
                try:
                    calls = json.loads(m.tool_calls)
                except json.JSONDecodeError:
                    # 坏数据不猜，跳过这一条，免得整个会话都发不出去
                    calls = []
                if calls:
                    item["tool_calls"] = calls
                    known.update(c.get("id") for c in calls if c.get("id"))

            # 有正文或有工具调用的才留。工具调用的那条 content 常常是空的。
            # 注意用 strip() 判断：纯空格的 content 在 Python 里是"真值"，
            # 但发给上游一样会被拒（"content must not be empty"）。
            if item["content"].strip() or item.get("tool_calls"):
                out.append(item)

        elif m.role == "tool":
            # id 没被声明过 = 半截数据，发出去会 400，宁可丢掉
            if m.tool_call_id and m.tool_call_id in known:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )

    return out
```

### 文件 5：`app/core/config.py` 片段

```python

    )

    # 默认值刻意留成"公开的默认值 + 空 key"。
    # 真实地址和 key 一律放 .env（已被 .gitignore 忽略）——
    # **密钥永远不要写进源码**：一旦进过版本库或分享出去，
    # 就只能当作已泄露，去后台吊销重发。
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    thinking: bool = False
    mock: bool = False
    port: int = 8787

    # ── 上下文预算 ─────────────────────────────────────
    # 一次请求最多带多少 token 的历史。超了就从最老的开始丢。
    #
    # 为什么是 16000 而不是"窗口大小乘个比例"？
    #   1. 网关没告诉我们窗口多大（/models 不返回 context_length）
    #   2. 实测一次普通问答约 1400 token 入、带工具约 3100
    #   3. 16k 大约能装 5-10 轮带工具的对话，够用，且便宜
    # 真调大也能改：.env 里设 CONTEXT_BUDGET=32000 即可，不用动代码。
    context_budget: int = 16000
```

---

## 已有测试（只列用例名，请判断还缺什么）

- test_under_budget_is_untouched
- test_long_history_is_trimmed_into_budget
- test_system_prompt_survives
- test_first_user_message_survives
- test_keeps_the_newest_messages
- test_tool_pairs_stay_intact
- test_pairing_survives_size_mismatch
- test_no_orphan_when_declaration_is_the_big_one
- test_never_returns_orphan_tool_at_head
- test_tool_pair_is_atomic_unit
- test_budget_larger_than_content_changes_nothing
- test_zero_or_negative_budget_is_a_noop
- test_tiny_budget_still_keeps_head_and_shrinks
- test_each_call_has_its_own_result
- test_estimate_is_in_the_right_ballpark
- test_estimate_counts_tool_arguments

---

## 输出格式

### 一、确定性 bug（会真的出错）
每条给出：触发条件 / 出错表现（报什么错、产生什么坏数据）/ 定位（函数+代码）/ 修法（修正后的代码片段）

### 二、边界情况风险（可能出错）
同上格式。

### 三、不变量核对表
对 6 条不变量逐条判断：**成立 / 不成立 / 有条件成立**，并说明依据。

### 四、测试缺口
现有测试没覆盖但应该覆盖的场景，按"最可能出事"排序。

### 五、如果只能改一处
给最高优先级的具体修改。

---

## 作者已自查过的三点（请验证结论，不要重复报告）

以下行为**是已知且被接受的**，请判断作者的结论是否成立，而不是当作新 bug 报告：

1. 巨大单元在**中间**时，`break` 会丢掉它之前的全部消息 —— 这是符合预期的，
   因为更老的消息本来就该丢。
2. `head + first_user` 是**无条件保留**的，即使它俩本身超预算。
   所以返回列表至少有两个元素，不会出现"只剩 system"的非法形状。
3. 孤儿 tool 消息（id 无对应声明）在**预算充足时不清理** ——
   那是 `messages_for_llm()` 的职责（读取时过滤），不是裁剪函数的职责。

**但作者没有验证下面这一点，请你重点分析：**

当**最新一轮**恰好是一个超过预算的巨大工具结果时（例如模型刚执行完
`cat 一个大文件`，结果 200000 字符），裁剪会把整个最新轮丢掉，
返回 `[system, user]`。这意味着模型看不到它刚拿到的工具输出，
可能陷入"我刚查过但什么都看不到"的状态，甚至重复调用同一个工具。

请分析：
- 这种情况下会发生什么？（结合 loop.py 每轮裁剪 + convo 被重新赋值的行为）
- 工具输出为什么不在这里被截断？责任应该落在哪一层？
- 如果必须在裁剪层缓解，最小改动是什么？（注意不能破坏不变量 3）

---

## 请特别关注（不限于此）

1. `split_units()` 对这几种情况的处理：
   - 连续多条 tool 消息
   - tool 消息的 id 不属于前一条 assistant
   - assistant 有 tool_calls 但结果丢失（进程崩溃只存一半）
   会不会产出非法序列？
2. 从尾部往前装时，某个"不可分割单元"**本身大于整个预算**会发生什么？
   `break` 会不会导致后面更小的单元也装不进去？
3. `head + first_user` 本身就超预算时，返回的列表形状是否合法？
   会不会返回只有 system 而没有 user 消息的列表？
4. `estimate_messages_tokens()` 漏算了什么？
   （考虑 content 是 list 的多模态格式、tool 消息的 name 字段、其它字段）
5. 每轮全量估算 token，历史几百条时是否有性能问题？
6. `convo` 在循环里先被裁剪、之后又被 append 工具结果——会不会有状态问题？
7. 历史里有**多条 system 消息**（开头一条、中间还有一条）时，`head_end` 处理是否正确？
8. 不变量 3：只检查"tool 有对应声明"够不够？
   "声明了但结果丢失"在 OpenAI 协议里是否同样致命？

请开始审查。**认为正确就简单说"正确"，不要展开；把篇幅留给问题。**
