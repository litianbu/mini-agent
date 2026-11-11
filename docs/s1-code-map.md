# S1 代码地图（配着代码看）

> 这份文档解决一个问题：**「这么多文件，从哪看起？」**
> 答案：从数据流看起，不要从文件列表看起。

## 一、一次请求的完整路径

用户在浏览器输入一句话，回车，到文字出现 —— 中间只经过这些函数：

```
浏览器  POST /api/chat {"question":"你好"}
   │
   ▼
api/routes/chat.py  post_chat()                    ← ① 收 HTTP，校验长度
   │          内层 event_stream()
   ▼
agent/loop.py       chat()                         ← ② 核心：Delta → 事件
   │
   ▼
agent/llm/client.py stream_deltas()                ← ③ 分发：真模型 / 假模型
   │
   ├── settings.is_mock = True  ──▶ llm/mock.py  mock_deltas()     逐字吐
   │
   └── settings.is_mock = False ──▶ llm/client.py real_deltas()
                                        │      httpx POST stream:true
                                        ▼
                                   llm/sse.py  iter_frames()        ← ④ 行→帧
                                        │
                                        ▼
                                   llm/client.py parse_chunk()        ← ⑤ 帧→Delta
   │
   ▼
agent/events.py to_sse()                       ← ⑥ 事件 → "data: {...}\n\n"
   │
   ▼
浏览器      api/index.ts  chatStream()           ← ⑦ 解析 SSE，逐个 yield
            App.tsx 的 switch              ← ⑧ 按 type 更新界面
```

**往回看更清楚**：前端只认识 5 种事件；后端把模型输出转成这 5 种事件；
中间所有复杂度都在"转换"上。

## 二、建议的阅读顺序

不要按文件名看。按这个顺序，每一步都只多一个新概念：

| 顺序 | 文件 | 行数 | 你要看懂的一件事 |
|---|---|---|---|
| 1 | `agent/events.py` | 76 | **契约**：5 种事件长什么样、怎么变成 `data: {...}` |
| 2 | `agent/delta.py` | 26 | 模型吐出来的一小块是什么形状 |
| 3 | `agent/llm/sse.py` | 48 | **最容易错的一段**：帧的边界是空行 |
| 4 | `agent/llm/client.py` | 110 | 怎么发请求、怎么把帧变成 Delta、出错怎么翻译 |
| 5 | `agent/llm/mock.py` | 51 | 没有 key 时谁在替模型说话 |
| 6 | `agent/loop.py` | 84 | **心脏**：Delta → 事件，计时、计字数 |
| 7 | `api/routes/chat.py` | 74 | 怎么把事件流转成 HTTP 响应 |
| 8 | `cli.py` | 79 | 同一份事件流，换个出口（终端） |

看完 1–6，你就理解了 agent 的"管道"；7–8 只是把它接到不同的地方。

## 三、`agent/loop.py` 的核心只有 25 行

整个项目最该看懂的就是这段（其余是注释）：

```python
yield StartEvent(...)                    # ① 报到

async for delta in stream_deltas(messages):   # ② 遍历模型增量
    if delta.reasoning:
        yield ReasoningEvent(delta=delta.reasoning)
    if delta.text:
        chars += len(delta.text)
        yield TextEvent(delta=delta.text)
    if delta.finish_reason:
        finish_reason = delta.finish_reason

yield DoneEvent(ms=..., chars=...)       # ④ 收尾
```

**这就是全部的 agent 了。** S3 之后它会长出 `while` 循环和工具分发，
但骨架不变：*拿输入 → 问模型 → 产出事件 → 看要不要继续*。

## 四、为什么文件要拆这么细

拆分前 `agent/llm/client.py` 有 169 行，装了四件事：HTTP 调用、SSE 拼帧、
假模型、异常翻译。四个概念挤在一个文件里，读的时候要不停切换上下文。

拆分后每个文件只有一个职责，**你可以只读你关心的那一个**：

- 只想搞懂流式？读 `agent/llm/sse.py`（48 行）
- 只想搞懂事件？读 `agent/events.py`（76 行）
- 只想看 mock 怎么写？读 `agent/llm/mock.py`（51 行）

判断标准很简单：**如果你能用一句话说清这个文件干什么，它就不该再拆。**

## 五、遇到看不懂的时候

按这个顺序自查：

1. 这个函数**输入什么、输出什么**？（看类型标注）
2. 它在上面那张图的**哪一步**？
3. 它**不知道**什么？（比如 `agent/llm/` 不知道 HTTP 之外的任何东西 ——
   不认识浏览器，也不认识 agent。边界清楚，代码就好懂）

第 3 点最有用。一个模块"不知道什么"往往比"知道什么"更能说明它的职责。
## 六、后端的分层（为什么目录长这样）

```
mini_agent/
├── main.py        组装：建 app、挂路由、托管前端产物、SPA 兜底
├── cli.py         终端入口
├── core/          基础设施（config）
├── agent/         ★ 核心，与 HTTP/DB 无关
│   ├── events.py  契约
│   ├── delta.py   增量
│   ├── loop.py    心脏
│   └── llm/       client / sse / mock
├── api/           HTTP 层
│   ├── deps.py    依赖注入（S5 的 get_db 放这）
│   └── routes/    chat / health
├── schemas/       请求响应模型
├── db/            ⏳ S5
└── services/      ⏳ S5
```

**一条依赖规则**（记住它就不会把项目写乱）：

```
api  →  services  →  agent
                 ↘  db
```

- `agent/` **绝不** import `api/` 或 `db/` —— 所以它能被 CLI 直接复用
- `api/` 不写业务逻辑，只做"拆请求 / 拼响应"
- `db/` 只管存取，不知道业务规则
- `services/` 是唯一允许同时碰 agent 和 db 的地方

违反这条规则会怎样？最典型的是：在路由里直接写 `await db.commit()`，
于是"存会话"这件事就有了两个入口，将来改规则时你会漏掉一个。

## 七、前端为什么用了 React Query，流式却还手写

前端的网络代码分两处，这不是重复，是刻意的分工：

| 文件 | 管什么 | 为什么 |
|---|---|---|
| `api/index.ts` | SSE 流解析 | React Query **管不了流**：一次请求、持续追加、没有"快照" |
| `api/client.ts` | 普通请求（`useHealth`） | 有缓存、去重、加载态的需求，正是 React Query 的主场 |
| `hooks/useChatStream.ts` | 流式状态机 | 页面不该关心 abort 时机和各种状态迁移 |

### React Query 解决的四件事（手写很烦的那种）

```ts
useQuery({
  queryKey: ["health"],      // 缓存键：同 key 只发一次请求
  queryFn: fetchHealth,
  staleTime: 5_000,          // 5 秒内复用缓存
  retry: 1,                  // 失败重试
});
```

1. **自动去重**：两个组件同时用 `["health"]`，只发一次请求
2. **缓存与过期**：`staleTime` 管"多久算旧"，切页面回来不用重新拉
3. **状态齐全**：`isPending` / `isError` / `data` 都给你，不用自己维护三个 useState
4. **失效重取**：`queryClient.invalidateQueries({queryKey:["sessions"]})`
   —— S5 里"存完会话让列表刷新"就一行

### 那为什么聊天不用 `useQuery`？

三个原因，缺一个都还能忍，三个凑一起就不行了：

- **`useQuery` 的返回值是"一次到位的快照"**，而聊天是无限追加的
- **没有缓存的意义**：每次对话都是新的，重取等于重发一遍
- **暂停/继续/取消**：它没有为"用户中途点停止"设计语义

所以流式用 `useMutation` 也不合适。**它就该是一个自定义 hook** ——
这也是所有 AI 聊天界面的通行做法（流式状态机 + 普通 CRUD 用 query 库）。

> 想省掉手写解析的 20 行？`eventsource-parser` 或
> `@microsoft/fetch-event-source` 可以替你做。
> 但我们的解析是 S1 的教学锚点，先留着；真做产品时可以换。
