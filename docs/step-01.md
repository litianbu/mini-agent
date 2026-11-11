# S1 · 流式输出：让字一个个蹦出来

> 这步只解决一个问题：**模型的话不再等全部生成完才显示，而是边生成边显示。**
> 就这一个能力，决定了你后面所有产品的"体感"。

## 一、它是怎么发生的（30 秒版）

普通请求：`POST /chat/completions` → 等 3 秒 → 拿到完整 JSON → 一次性打印。

流式请求：body 里加一个 `stream: true` → 服务端立即返回响应头，
然后**边算边发**，每一小块长这样：

```
data: {"choices":[{"delta":{"content":"你"}}]}

data: {"choices":[{"delta":{"content":"好"}}]}

data: [DONE]
```

帧与帧之间用**空行**隔开。就这么简单，没有魔法。

## 二、三个必须记住的点

1. **`stream: true` 是唯一开关。** 同一个接口、同一份 messages，加这一个字段，行为就从"一次性"变成"流式"。

2. **必须按"空行"切帧，不能按行切。**
   SSE 的帧边界是**空行**，不是换行 —— 一帧可能有多行 `data:`。
   这也是为什么 Python 里用 `aiter_lines()` 之后还要自己攒 `data_lines`：
   库只帮你切成行，"帧"这个概念得你自己拼。

3. **`[DONE]` 是协议的结束标记，不是内容。** 收到就 `return`，别把它当正文发给用户。

## 三、代码地图（全部在 `apps/backend/mini_agent/`）

> **先看 [`s1-code-map.md`](s1-code-map.md)** —— 那里有一张"一次请求经过哪些函数"的图，
> 以及建议的阅读顺序。下面只是文件清单。

| 文件 | 行数 | 干什么 |
|---|---|---|
| `events.py` | 76 | **契约**。5 种事件的 Pydantic 模型 + `to_sse()` |
| `delta.py` | 26 | 模型吐出的那一小块（reasoning / text / finish） |
| `sse.py` | 48 | **最容易错的一段**：把行拼成帧（边界是空行） |
| `llm.py` | 110 | 发请求、分发真假模型、帧 → Delta、异常翻译 |
| `mock.py` | 51 | 没有 key 时的假模型，逐字吐字 |
| `chat.py` | 84 | **心脏**：Delta → 事件，计时、统计字数（核心只有 25 行） |
| `api.py` | 120 | FastAPI：把事件流转成 SSE 响应 |
| `cli.py` | 79 | 终端版：同一份事件流，打印出来 |

帧解析的核心就这几行（`sse.py`）：

```python
async for line in lines:
    if line != "":                       # 还没到帧边界，先攒着
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        continue
    if buffer:                           # ← 空行 = 一帧结束
        yield "\n".join(buffer)
        buffer.clear()
```

## 四、一个坑（你一定会遇到）

`httpx.ConnectError` 默认只说"连接失败"，真正原因藏在异常链里
（地址写错 / 服务没起来 / 端口不对）。
`llm.py` 里的 `explain()` 把它翻译成人话，以后看到那句就知道是网络问题，不是代码问题。

另一个坑是**中文乱码**：一个汉字在 UTF-8 里占 3 字节，可能被切在两个网络包里。
Node 要 `TextDecoder(..., {stream: true})`；Python 这边 `aiter_lines()` 已经帮你处理了 ——
但你自己用 `aiter_bytes()` 时就必须自己缓冲。

## 五、验收

```bash
cd mini-agent

# 终端里开两个窗口
pnpm dev:backend            # 后端 :8787（没有 key 会自动 MOCK）
pnpm dev:frontend            # 前端 :5180

# 或者只验证命令行
pnpm cli "用一句话介绍你自己"
```

浏览器打开 <http://localhost:5180>，输入一句话回车 ——
文字应该是**一段段冒出来**，而不是憋几秒后整段刷出。

## 六、为什么现在还没有工具、没有记忆

`messages` 数组已经按多轮格式写好了（`role/content`），只是每次都只塞一条 user 消息。
S3 会在同一个循环里，把"模型的回复"和"工具的结果"也塞进去 ——
**这就是 agent 和你现在这个聊天程序的全部区别。**

## 七、下一步预告

**S2 前后端分离**：把现在的 Node 后端换成 Python，并把仓库拆成 `apps/backend` + `apps/frontend`。
那一步不加新功能，但决定了后面每一步能不能顺利长大。

再往后 ——

**S3 工具循环**：让模型在回答前先说"我要读一下 pyproject.toml"，
你替它读，把内容塞回去，它再开口。那 20 行是整个 agent 的心脏。