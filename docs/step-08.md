# 第八步：HTTP 加固（限流 / 超时 / 取消传播）

S7。前三步都在加功能，这一步反过来：**保护已有的功能不被拖垮**。

三件事看起来无关，其实都在回答同一个问题：

> 一个请求占着连接、worker、钱不放的时候，怎么让它停下、或者根本别开始？

## 一、限流：同一个会话只能有一个请求在跑

### 要解决的是什么

用户手快连点两次「发送」，后端会同时跑两个请求：

```
两个请求同时读同一份历史 → 各自生成 → 各自往库里写
→ 消息顺序乱了，侧栏排序会跳
```

而且更贵：一次 agent 请求内部最多调 **8 次模型**，每次都带上万 token 的历史。

```
连点 5 次 × 每次最多 8 轮 = 40 次模型调用
```

### 为什么按会话限流，不是按 IP

```
按 IP 限流   → 防外人刷
按会话限流   → 防"自己跟自己撞车"
```

agent 场景里后者更常见：一个人开两个标签页，或者手快连点。两者不冲突，这里只做后者。

### 两个位置很关键

**① 抢锁必须在「写用户消息」之前**

```python
if not session_limiter.acquire(session_id):
    raise HTTPException(429, detail="这个会话的上一条回答还在生成，请稍候再发")

try:
    history = await messages_for_llm(db, session_id)
    await session_service.add_message(db, session_id, "user", body.question)
except Exception:
    session_limiter.release(session_id)   # 抢到锁但后面出错了，得在这里放
    raise
```

如果放在写库之后，被拒绝的请求也会把提问存进去，**留下一条永远没有回答的孤消息**。

**② 释放必须在生成器的 `finally` 里**

```python
async def event_stream():
    try:
        async for chunk in bridge(_stream(), request):
            yield chunk
    finally:
        session_limiter.release(session_id)
```

为什么不是 `return` 前放：`StreamingResponse` 是**立刻返回**的，生成器之后才跑。
所以释放只能挂在生成器上，而 `finally` 覆盖正常结束、出错、用户断开三种情况。

漏了的后果：**这个会话永久 429**，用户只会觉得"程序坏了"。

### 一个诚实的局限

```python
self._active: set[int] = set()      # 进程内存
```

单进程没问题；`uvicorn --workers 4` 时每个 worker 各记一份，仍可能撞车。
真要严谨得换 Redis（`SET NX EX`）。注释里写明了，不假装没这回事。

## 二、超时：两个超时管的是不同的事

这是这一步最重要的认知，**它们是叠加关系，不是替代关系**：

| 配置 | 管什么 | 实现 |
|---|---|---|
| `llm_read_timeout` | **两帧之间的间隔** | `httpx.Timeout(read=...)` |
| `agent_timeout` | **整件事的总时长** | `asyncio.timeout(...)` |

```
只配 read 超时 → 模型一直慢慢吐字，吐 20 分钟也发现不了
只配总时长     → 卡在某一帧上不动，要等 300 秒才知道
两个都配       → 60 秒没动静就砍；整体超 300 秒也砍
```

### 原来的 `read=None` 为什么危险

```python
timeout = httpx.Timeout(connect=15.0, read=None, write=30.0, pool=15.0)
```

`read=None` 看起来"宽容"（生成多久都等），实际是漏洞：

```
网关半死不活时：
  TCP 连着            → connect 不超时
  偶尔发个心跳帧        → read 永远不超时（"有数据"）
  就是不输出正文
→ 请求永远挂着，占着 worker、连接、内存
```

### 但也不能设太小

模型思考（reasoning）时，第一块数据可能要 10-20 秒。`read=5` 会把正常请求全砍断。

**这就是超时的难点：安全值和正常值之间的区间可能是空的。** 取 60 秒——远大于正常思考，又远小于"卡死"。

### 超时后走 ErrorEvent，不走 DoneEvent

```python
except TimeoutError:
    yield ErrorEvent(message=f"这次处理超过了 {settings.agent_timeout:.0f} 秒上限，已停止。"
                             "可以试着把任务拆小，或在 .env 里调大 AGENT_TIMEOUT。")
    return
```

**如果同时发 `done`**，前端会以为回答正常结束，把半截内容当成完整答案。

提示里带上配置名，用户知道怎么改——报错信息没说"怎么办"等于没说。

## 三、取消传播：从"轮询"改成"主动打断"

### 原来的写法为什么不够

```python
async for event in chat(convo):
    if await request.is_disconnected():    # ← 必须先拿到事件才检查
        return
```

这是**轮询**，只在产出新事件时才有机会检查。可是最需要切断的时刻，
恰恰是**没有事件**的时候：

```
用户点「停止」→ 关掉页面
→ 模型侧还在思考（或卡死），一个字都不吐
→ 卡在 async for 上，永远走不到那行检查
→ 后端继续等，直到 300 秒超时
→ 用户以为停了，服务器还在为他烧钱
```

### 解法：两个任务并发，谁先结束就取消另一个

```
        ┌── produce() ── 产流 → 队列 ──┐
        │                              ↓
event_stream ───────────────────── 消费 → 发给浏览器
        │                              ↑
        └── watch() ── 每 0.5 秒问一次断开 ─┘
                              │
                     检测到断开 → 塞结束标记
                              ↓
                     finally: 取消还在跑的那个
```

取消信号会一层层传下去：

```
bridge 的 producer Task
  → _stream() 生成器
    → chat() 生成器
      → stream_deltas() 的 httpx 流    ← 连接在这里关掉
```

### 三个实现细节

**① 结束标记用独有对象，不用 `None`**

```python
_END = object()      # None 可能是合法数据，标记不能和数据混淆
```

**② 取消后必须 `await`，不能只调 `cancel()`**

只调 `cancel()` 就返回，任务还在后台收尾，事件循环关闭时会报
**"Task was destroyed but it is pending"**。

**③ `except BaseException` 而不是 `Exception`**

`CancelledError` 在 Python 3.8+ **不是** `Exception` 的子类，用 `Exception` 兜不住。

## 四、踩的坑：`cancel()` 杀不掉真进程

`cancel()` 只负责"打断等待"，不负责"清理资源"。实测：

```
启动 `sleep 30` 子进程 → 取消协程 → 协程结束了
❗ 子进程还活着 —— cancel() 没杀掉它
```

这就暴露了 `shell.py` 里的一个真 bug：

```python
except asyncio.TimeoutError:
    proc.kill(); await proc.wait()      # 超时路径有
except Exception as exc:                 # ← CancelledError 从这里穿过去了
    return f"执行失败：{exc}"
```

用户点停止时，`CancelledError` 穿过，**进程没人杀，成了孤儿**。

补上：

```python
except asyncio.CancelledError:
    proc.kill()
    await proc.wait()
    raise          # 取消必须继续往外传，否则上层以为正常结束了
```

**清理要分层次看**：

| 层次 | 对象 | 取消手段 | `cancel()` 管用吗 |
|---|---|---|---|
| 协程 | async 函数 | `Task.cancel()` | ✅ 就是它 |
| 网络连接 | TCP socket | `async with` 退出 / `aclose()` | ⚠️ 间接（靠 finally） |
| 操作系统进程 | fork 出来的进程 | `proc.kill()` | ❌ 完全不管 |
| 数据库事务 | 未提交的改动 | `rollback()` | ❌ 不管 |

## 五、两种超时之外：`abort()` 为什么能终止后端

一个容易被误解的点：前端的 `abort()` 不只是"本地抛个错"。

```js
const ctrl = new AbortController();
fetch(url, { signal: ctrl.signal });
ctrl.abort();      // ① JS 层：Promise 抛 AbortError
                   // ② 网络层：浏览器把连接关了  ← 后端只能看到这个
```

实测（Node 起一个 SSE 服务，客户端 abort）：

```
客户端：调用了 abort()
客户端：reader 抛出 AbortError
服务端：收到 close 事件，说明连接真的断了
```

**服务端根本不知道有 `AbortController` 这个东西**，它只感知到 TCP 断了。
这才是两个系统能配合的原因——不需要共同语言，只需要连接状态。

整条链路：

```
ctrl.abort() → 浏览器关 TCP → 后端 ASGI 收到 http.disconnect
→ is_disconnected() 返回 True → cancel() → CancelledError 逐层
→ async with 退出 → httpx 关模型连接 → 模型停止
```

三个角色分工：

| 角色 | 职责 |
|---|---|
| `abort()` | **制造事实**：连接断了 |
| `is_disconnected()` | **发现事实**：连接断了 |
| `cancel()` | **执行处置**：停下来 |

## 六、前端也要收尾

后端做了取消传播，但**连接断不断是前端决定的**。所以前端补了三处：

### ① 卸载时中止（最关键）

`useChatStream` 里原来**完全没有 `useEffect`**：

```
生成中途点侧栏去看会话列表 → 组件卸载 → 但 fetch 还连着
→ 后端以为用户还在看 → 跑完整个流程 → 为已经离开的人烧钱
```

```tsx
useEffect(() => {
  return () => { aborter.current?.abort(); };
}, []);
```

### ② 中止时释放 reader

```tsx
try {
  while (true) { const { done, value } = await reader.read(); ... }
} finally {
  try { await reader.cancel(); } catch { /* 已经断了就别再抱怨 */ }
}
```

不 `cancel()` 的话，那条流会一直挂着占连接。

### ③ 不再一律说"网络错误"

```tsx
setError(`网络错误：${(e as Error).message}`)     // 之前
```

后端返回的 429/422 已经被翻译成人话了，这个前缀会把"服务端在拒绝"
说成"网络有问题"，排查方向直接跑偏。改成按类型区分：

```tsx
function describe(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return "连不上后端，检查服务是否在运行";     // 真的网络故障
  }
  return msg || "请求失败";                      // 后端已翻译好的
}
```

## 验证

| 检查 | 结果 |
|---|---|
| 后端测试 | 77 passed（mock 78） |
| 前端测试 | 38 passed |
| 限流端到端 | 并发两请求：`200` + `429` ✓ |
| 取消端到端 | 读 3 帧后断开，锁立即释放 ✓ |
| **变异测试**：删总时长超时 | 测试**挂死 25 秒**被强杀 ✓ |
| **变异测试**：退回轮询 | 静默场景超时失败 ✓ |
| **变异测试**：删卸载中止 | 卸载测试失败（1019ms）✓ |

**变异测试这次特别有说服力**：删掉总时长限制后，测试不是"断言失败"，
而是**永远不返回**。这恰好证明没有超时的后果是彻底卡死，不是报错。

## 还没做的

- **限流是进程内状态**，多进程部署要换 Redis
- **没有全局限流**（只做了会话级），防不住恶意刷接口
- **切换会话会中止当前回答**，且半截内容不入库——用户切回来会以为回答消失了
- **httpx 每次请求新建 client**，失去连接复用（一次对话几次请求，影响不大）

## 一句话总结

```
限流  → 控制"同时有多少事在做"
超时  → 控制"一件事最多做多久"
取消  → 控制"这件事立刻停下"
```

三者都是资源保护，也都是后端工程的日常——任何调外部 API、
有成本、跑长任务的服务都要处理这三个。
