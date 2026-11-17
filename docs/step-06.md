# 第六步（上）：让 token 用量看得见

S6 要解决的是"聊久了会崩"。但动手裁剪之前，得先能量出到底用了多少。

**这一步不改变任何行为**，只是把本来就有、但被扔掉的信息捡回来。

## 为什么需要它

现在每次请求都把**全部历史**发给模型：

```python
history = await messages_for_llm(db, session_id)   # 一条不落
convo = [*history, {"role": "user", "content": body.question}]
```

没有长度控制。到某天，历史超过窗口上限，接口返回 400。

**最麻烦的是这个会话会永久废掉**：历史存在数据库里删不掉，
下次再问还是把超长的历史重发一遍，还是 400。

## 三种"长度限制"，别混在一起

| | 管什么 | 单位 | 现在 |
|---|---|---|---|
| `MAX_OUTPUT` | **一条**工具输出 | 字符 | 4000 |
| `MAX_READ_LINES` | 单次读文件 | 行 | 400 |
| **上下文预算** | **一次请求**发给模型的全部内容 | token | 还没有 |

前两个是工具自己的护栏，第三个是接下来要加的。
**"一条工具输出"和"一次请求的总量"不是一个数量级**——
按实测比例，一条满额的 bash 输出约占 16k 预算的 14%，
所以一轮里调 5 次工具就能吃掉七成。

## 关键发现：模型早就告诉我们了

流式响应里，最后一帧带 `usage`：

```json
{"choices":[{"index":0,"delta":{}}],"usage":{"prompt_tokens":1342,...}}
```

而且代码里 `stream_options: {"include_usage": True}` 早就开了。
但 `parse_chunk` 里有这么一句：

```python
if not (reasoning or text or finish or calls):
    return []      # ← usage 就在这里被丢掉了
```

**等于模型告诉你花了多少钱，你说不要。**

## 踩的坑：不能照文档写

OpenAI 文档说收尾帧是 `choices: []`，所以第一版我写成：

```python
if not choices:
    return [Delta(usage=u)] if u else []
```

结果 usage 一直是 0。抓了原始响应才看清，**这个网关的收尾帧是**：

```json
{"choices":[{"index":0,"delta":{}}],"usage":{...}}
```

`choices` **不空**，里面是个空 delta。于是它走进了正常分支，
又因为"什么都没解析到"被丢掉。

修法是别靠 `choices` 判断，**直接看有没有 `usage` 字段**：

```python
u = obj.get("usage")
usage = Usage(...) if u else None
# 然后把 usage 挂在解析出来的 delta 上，不管 choices 是什么形状
```

**教训：协议要以实际抓包为准，文档只是参考。**

## 累加，不是取最后一个

一次提问可能调用模型多次（每轮工具调用一次）。每个响应都有自己的 `usage`。

如果只留最后一个，看到的只是最后一轮的数字。所以要累加：

```python
usage_total = Usage()
...
if delta.usage:
    usage_total = usage_total + delta.usage
```

实测验证：一次带工具调用的提问，2 轮，`prompt=3090`。
单轮约 1342 + 带工具输出的 1748，加起来对得上。

## 删掉的：本地估算函数

我一开始还写了 `estimate_tokens` / `estimate_messages_tokens`，
理由是"下一步裁剪要用"。但**这一步根本用不到它**，业务代码里零调用，
只有测试在跑 —— 测试全绿会让人误以为功能生效了，其实一行都没执行。

这种叫**投机代码**：为"以后可能用到"而存在，不产生价值，还占你的注意力。删了。

不过有个**测量结果值得留着**（S6-2 定阈值要用）：

> 拿 570 个汉字去问模型，`usage` 报 330 token，即 **1 token ≈ 1.73 汉字**。

这不是估算，是实测。等 S6-2 真要估算时，按这个系数校准就行。

## 验证

```bash
curl -sN -X POST localhost:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"一句话说明什么是上下文窗口"}'
# → done 事件：prompt=1342 completion=75 total=1417
```

界面上，每条回答下方的统计行会多一段：

```
stop · 78 字 · 1178ms · 1 轮 · 1417 token（入 1342 / 出 75）
```

`total_tokens` 为 0 时不显示（mock 模式没有真实用量），免得误导。

## 这一步之后能回答的问题

- 现在这个会话有多少 token？
- 一次工具调用值多少 token？
- 预算该设多少？

**先能量，才能定阈值。** 下一步（S6-2）才是真的动手：
工具输出治理（L0）+ 滑动窗口兜底（L1）。
