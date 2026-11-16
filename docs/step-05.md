# 第五步：会话持久化 —— 刷新页面不丢对话

前四步做完，agent 能干活了，但**一刷新页面全没了**。
这一步把对话存进 MySQL，加上侧栏的历史列表。

## 数据长什么样

两张表，一对多：

```
sessions  ──┬── messages
 id (int)   │    id
 title      │    session_id  → sessions.id（ON DELETE CASCADE）
 created_at │    role        user / assistant / tool
 updated_at │    content
            │    reasoning
            │    created_at
```

**为什么 id 用自增整数而不是 UUID**：
侧栏按 `updated_at` 排序、按 id 取消息，整数索引比 36 字符的字符串小得多、快得多。
UUID 的好处是"分布式生成不冲突"，但这里只有一个数据库，用不上。

（顺便踩了个坑：库里残留着早期用 UUID 的旧表，`create_all` 不会改已存在的表，
所以接口直接报 `unable to parse string as an integer`。删表重建才对得上。）

## 分层：路由 / 服务 / 模型

```
api/routes/chat.py       ← HTTP：存库时机、SSE 包装
api/routes/sessions.py   ← HTTP：列表、详情、删除
services/session_service.py ← 业务：怎么存、怎么查（CLI 也能用）
models/                  ← 库结构
```

**为什么中间要加一层 service**：
如果存库逻辑写在路由里，CLI 就没法复用，将来换 JSONL 存储要改两个地方。
现在换存储只改 `session_service.py`，路由一行不动。

## 存的时机：一个容易忽略的细节

```python
# 用户消息：流开始前就存
await session_service.add_message(db, session_id, "user", body.question)

async for event in chat(convo):
    ...
    yield to_sse(event)

# 助手消息：流结束后存（必须收齐才有完整回答）
if finished:
    await _save_answer(db, session_id, text_parts, reasoning_parts)
```

用户消息先存，是因为**万一模型挂了，至少知道用户问了什么**。
助手消息必须等流结束，边流边存只能存到半句话。

## session_id 怎么传给前端

新建会话时后端才分配 id，但响应已经是流了，没法再改响应头。
所以把它塞进 `start` 事件：

```python
if isinstance(event, StartEvent):
    event = event.model_copy(update={"session_id": session_id})
```

前端收到后把地址栏换成 `/sessions/1` —— 刷新就能回到同一段对话。

## 前端：一个组件，两种进法

```
/              → 新会话（没有 id，后端分配）
/sessions/:id  → 历史会话（先读库，再接着聊）
```

不写两个页面，是因为界面、输入框、滚动逻辑完全一样，
复制一份以后改样式要改两处，迟早会漏。

```tsx
const { id } = useParams();
const sessionId = id ? Number(id) : undefined;
const { data: detail } = useSession(sessionId);

// 库里的消息 → 气泡。useMemo 不能少：
// 每次渲染都新建数组的话，会把已经流式写进去的内容冲掉
const initialBubbles = useMemo(() => ..., [detail]);
const chat = useChatStream(initialBubbles, sessionId);
```

## 这里才真正用上 React Query

`api/client.ts` 顶部早就写着"S5 的会话列表正好用它"，现在兑现了：

```tsx
useSessions()      // GET /api/sessions
useSession(id)     // GET /api/sessions/1
useDeleteSession() // DELETE + 成功后 invalidate 列表
```

对比聊天流：那边是一次请求持续推送、数据无限追加，没有"缓存"和"重取"的概念，
所以留在 `useChatStream` 里手写。**工具选对场景才有意义。**

## 两个后端细节

**① 列表用一次 join，不要 N+1**

```python
select(ChatSession, func.count(Message.id))
    .outerjoin(Message, ...)
    .group_by(ChatSession.id)
```

如果先查会话再逐个 count，20 个会话就是 21 次数据库往返。

**② 按请求开 session**

```python
async def get_db():
    async with SessionLocal() as session:
        yield session
```

`AsyncSession` 不是并发安全的，两个请求共用一个会互相干扰事务。

## 验证

```bash
# 存一条
curl -sN -X POST localhost:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"这个项目用什么数据库？"}'
# → start 事件里有 session_id: 1

# 读回来
curl -s localhost:8787/api/sessions/1

# 续聊（模型能记得上一句）
curl -sN -X POST localhost:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"我刚才问了什么？","session_id":1}'
# → 「你刚才问我：这个项目用什么数据库？」
```

界面上：左侧「历史会话」→ 列表（标题 + 相对时间 + 条数 + 删除）→ 点进去接着聊。

## 已知取舍

**工具调用过程没落库。** 只存了 user / assistant 的正文。
原因是 `tool_calls` 要和 `role="tool"` 的结果消息严格配对，
存一半的话，回放时把残缺的 tool 消息发给上游会直接报错。

代价：重新打开会话后，模型不知道之前调用过什么工具、看到了什么结果。
要修的话，得在 `loop.py` 里把中间的 tool 消息也吐出来存 —— 留给后面（S6 上下文管理）。

## 测试

`tests/test_s5_sessions.py`，6 个用例，**连真库**：

```python
async def db():
    async with database.SessionLocal() as session:
        yield session
    await database.engine.dispose()   # ← 这行不能少
```

`dispose()` 的原因：pytest-asyncio 给每个测试建新事件循环，
但连接池是模块级、跨测试复用的。不清理就会在第二个测试里炸
`Event loop is closed`。

重点用例是**级联删除**：删会话要连消息一起删，
否则 `messages` 表会攒下一堆没人认领的行，越积越多还查不出来。

## 修过的两个真 bug（前端）

S5 上线后立刻暴露两个问题，都是"渲染测试测不出来"的那一类。

### bug ①：从历史会话进去偶尔空白

**症状**：点历史会话，接口 200 也返回了数据，但页面是空的。而且**不是每次**——
同一个会话第二次点进去往往又正常。

**原因**：`useState` 只在**首次渲染**读初始值。

```tsx
// 错的写法
const { data: detail } = useSession(sessionId);   // 首帧是 undefined
const bubbles = useChatStream(toBubbles(detail?.messages), sessionId);
//                 ↓ 内部
//                 useState(initialBubbles)  ← 空数组，之后再也不会更新
```

首帧请求还没回来 → `initialBubbles` 是 `[]` → `useState` 把它当初始值锁住。
数据到了、组件重渲染，但 `useState` 不会重新读初始值，页面就一直空着。

**为什么"偶尔"正常**：React Query 有缓存。第二次进同一个会话，
首帧就能拿到缓存数据，初始值非空，于是正常。首次进入必现。

**修法**：不是"用 useEffect 把新数据塞进 state"（那样要处理历史数据和
流式数据的覆盖顺序，很容易引入新 bug），而是**让组件晚点出生**：

```tsx
// 数据没到 → 显示加载态，不挂载聊天组件
if (sessionId !== undefined && isLoading) return <Spin />;

// 数据到了才挂载，此时初始值就是对的
return <ChatView key={sessionId ?? "new"} initialBubbles={toBubbles(detail?.messages)} />;
```

`useState(某个异步数据)` 只有在这种结构下才是安全的。
**"让组件晚点出生"比"事后同步状态"简单得多。**

### bug ②：新会话发完消息，刷新就没了

**原因**：在 `/` 发消息，后端分配了 session_id，前端只在内存里记着，
地址栏一直停在 `/`。刷新 → 又是全新会话。

**修法**：拿到 id 就把地址栏换掉。

```tsx
useEffect(() => {
  if (sessionId == null && chat.sessionId != null) {
    window.history.replaceState(null, "", `/sessions/${chat.sessionId}`);
  }
}, [chat.sessionId, sessionId]);
```

**为什么不用 `navigate()`**：`/` 和 `/sessions/:id` 是两个路由，
`navigate` 会让组件卸载重挂 → 正在进行的流断掉、已收到的文字丢失。
`replaceState` 只改地址栏、不通知路由，组件不受影响。

## 补了前端测试（这次是被迫的）

前面几步只用"SSR 渲染测试"验证界面，它只能验证**给定数据画出什么**，
验证不了"数据晚一步到"这种时序问题 —— 上面两个 bug 都是这么漏掉的。

所以引入了 vitest + happy-dom + Testing Library：

```bash
pnpm --filter @mini-agent/frontend test
```

`src/pages/NewSessionPage.test.tsx` 里 5 个用例，其中两个就是这两个 bug 的回归测试：

```tsx
it("接口数据晚一步到达时，消息仍然要渲染出来", async () => {
  mockSessionApi(30);          // ← 延迟 30ms，制造"数据晚到"
  renderAt("/sessions/1");
  await waitFor(() => {
    expect(screen.getByText("第一个问题")).toBeTruthy();
  });
});
```

**写完必须验证测试真能抓到 bug**：把代码临时改回旧写法，确认测试变红，
再恢复。否则你可能写了一个永远通过的假测试。

两个测试都这样验证过：

| 临时改回 | 结果 |
|---|---|
| 不等待数据直接挂载 | 「消息仍然要渲染出来」失败 ✓ |
| 去掉 replaceState | 「URL 变成 /sessions/N」失败 ✓ |

两个容易踩的测试配置坑：

- **必须有真实 `<Route>`**：直接渲染组件的话 `useParams()` 是空的，
  测出来的是"新会话"分支，不是历史会话。
- **antd 的按钮文字在 span 里**：`getByText("发送")` 找不到，
  要用 `getByRole("button", { name: /发\s*送/ })`。
