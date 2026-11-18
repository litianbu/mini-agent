# 第九步：接入 MCP（外部工具）

S10。前面所有工具都是**写在代码里**的：

```python
ALL_TOOLS = [READ_FILE, WRITE_FILE, EDIT, GLOB_SEARCH, GREP, RUN_BASH]
```

想加第七个工具（查天气、查数据库、发消息），得写文件、实现 `run()`、
写 schema、加进列表、重启后端。**加 10 个工具就重复 10 遍。**

而且每个 agent 项目都要各干一遍——这就是 **M×N 问题**：

```
M 个 AI 应用 × N 个工具 = M×N 套集成代码
```

## MCP 是什么

**MCP（Model Context Protocol）是一套让 AI 应用接入外部工具的通用协议。**

一句话：**AI 应用和外部工具之间的 USB 接口标准。**

```
没有 MCP：              有 MCP：
应用A ─┬─ 天气           应用A ─┐
       ├─ 数据库                 ├─ MCP ─┬─ 天气服务
       └─ 飞书                   │       ├─ 数据库服务
                          应用B ─┘       └─ 飞书服务
应用B ─┬─ 天气
       ├─ 数据库          = M+N 套
       └─ 飞书
= M×N 套
```

**关键理解**：MCP 不是工具，是协议。工具是"实现了这个协议的服务"。

**对模型来说，MCP 工具和本地工具完全一样**——都是 tool 清单里的一条，
都会产生一次 `tool_call`。模型根本不知道这个工具从哪来。

MCP 除了 tools，还有 resources（可读数据）和 prompts（预设模板）两个原语，
这一步只用了 tools。

## 协议本身没什么神秘的

就是 **JSON-RPC 2.0**，三种报文：

```json
// 请求（带 id，要回话）
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}

// 成功响应
{"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}

// 失败响应
{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"..."}}

// 通知（没有 id，不回话）
{"jsonrpc":"2.0","method":"notifications/initialized"}
```

**stdio 传输的分帧规则**：一行一条 JSON，换行符结尾。

> 注意：**不是** LSP 那种 `Content-Length` 头。这是最常见的误解——
> 很多人拿 LSP 的经验来接 MCP，结果卡在"为什么读不到"。

握手三步：

```
客户端                          服务器
  │  initialize (id=1)           │
  │ ───────────────────────────▶ │
  │  result: {serverInfo...}     │
  │ ◀─────────────────────────── │
  │  notifications/initialized   │  ← 通知，无 id，不回话
  │ ───────────────────────────▶ │
  │  tools/list (id=2)           │
  │ ───────────────────────────▶ │
```

## 为什么手写，不用官方 SDK

和不用 LangChain、不用 OpenAI SDK 同理：**SDK 会把该学的东西藏起来**。

```python
# SDK 版（真实项目就这么写）
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("get_weather", {"city": "北京"})
```

进程启动、分帧、握手、id 匹配、关闭清理——全替你做。

**我们手写了 291 行**（`protocol.py` 77 + `client.py` 214）。

手写的价值不是"以后也这么写"，而是**先理解，再使用**：

```
直接上 SDK → 能用，但说不出：
             · 为什么响应要按 id 匹配
             · 为什么 stderr 不能接管道
             · 为什么服务端要 flush

手写一遍后 → 这些坑都踩过了
             → 再上 SDK，它做的每一步你都看得懂
             → 出问题能定位，而不是只会重启
```

## 为什么自己写天气服务器

三个理由：

1. 现成的天气服务要真实 API key，跑不通就没法学
2. 装第三方等于 `npx -y` 下载并运行别人的代码，**而且它有你的文件系统权限**（供应链风险是真实的）
3. **自己写一遍服务端，协议才真正学明白**——光当客户端，你看不清"响应在服务端是怎么产生的"

它就是一个普通程序：从 stdin 读一行 JSON，往 stdout 写一行 JSON。
没有 HTTP、没有框架、没有依赖。

## 三个必须做对的地方

### ① 响应按 id 匹配

```python
if msg.get("id") != request_id:
    continue      # 不是我要的
```

一个连接上可能有多个请求在飞，**收到的响应顺序不保证**。
不按 id 认，就会把 A 的结果当成 B 的。

### ② `stderr` 绝不能接管道

```python
stderr=None,      # 让它直接打到终端
```

接了管道又不读，**缓冲区满了服务器会卡死**。这是经典死锁。

### ③ 服务端必须 `flush()`

```python
sys.stdout.write(json.dumps(reply) + "\n")
sys.stdout.flush()      # ← 不 flush，客户端永远等不到
```

Python 的 stdout 是块缓冲，不 flush 数据卡在缓冲区里——
**客户端看起来就是"服务器不回话"**。

## 架构：传输层 vs 适配层

这是这一步最值得记住的分层：

```
官方的 mcp SDK        ← 传输层：你不用写（我们手写了 291 行）
────────────────────────────────────────────────────
你的适配层            ← 你必须写（我们写了 215 行）
   ├─ schema 转换
   ├─ 工具合并 + 命名空间
   └─ 调用路由
────────────────────────────────────────────────────
你的 agent 主循环     ← 本来就有的（S3 写的）
```

**`convert.py` 和 `registry.py` 是任何项目都得自己写的**，
因为它们是「**你的应用**」和「MCP」的接缝：

1. 工具名怎么加命名空间
2. 怎么把本地工具和 MCP 工具合成一个清单
3. 模型调工具时怎么路由到"本地函数"还是"MCP 服务器"

**这三件事 MCP 不管，SDK 也不管，只有你知道。**

真实项目的结构是同一个形状，只是传输层换成 SDK。
LangChain/Cursor/Claude Code 的 MCP 适配器都有这么一层。

## 两个生态的字段名不一样

```
MCP（Anthropic 那套）            OpenAI function calling
─────────────────────            ───────────────────────
{"name": "get_weather",          {"type": "function",
 "description": "...",            "function": {
 "inputSchema": {...}              "name": "get_weather",
                                  "description": "...",
                                  "parameters": {...}
                               }}
```

**`inputSchema` → `parameters`**，就是这一步的全部转换。

## 工具名必须加命名空间

```python
qualified_name("weather", "get_weather")   # → "mcp__weather__get_weather"
```

三个理由：

```
① 防撞车 —— MCP 服务器也可能提供叫 bash 的工具
   直接混进 ALL_TOOLS 会静默覆盖本地的，非常难查
② 路由简单 —— execute() 看到 mcp__ 开头就知道转发
   不用维护"哪些名字属于哪个服务器"的映射表
③ 调试友好 —— 一眼看出工具从哪来
```

这是 Claude Code 等工具的做法（它们用 `mcp__server__tool`）。

拆名字时注意用 `split("__", 1)` 而不是 `split("__")`：
工具名本身可能含下划线，多切一刀就拆错了。

## 接进主链路

```python
# tools/__init__.py
async def schemas() -> list[dict]:
    return [tool.schema() for tool in ALL_TOOLS] + await mcp_servers.schemas()


async def execute(name: str, arguments: str) -> str:
    if name.startswith(PREFIX):          # ← MCP 先分流
        args = json.loads(arguments)
        return await mcp_servers.call(name, args)
    tool = _BY_NAME.get(name)            # ← 本地老路
    ...
```

`schemas()` 从同步变成异步——**问 MCP "你有哪些工具"是进程往返**，必须 await。

## 两处刻意的容错

### ① 单个服务器挂了，不能拖垮全部

```python
for server in self.servers:
    try:
        ...
    except (MCPError, OSError, TimeoutError) as exc:
        print(f"[mcp] 服务器 {server!r} 不可用，跳过：{exc}")
        continue
```

一个配置错的服务，不该让整个对话发不出去。
**加新功能把老功能搞坏**是最常见的回归，所以专门写了测试守这一条。

### ② MCP 调用出错返回错误文字，不抛异常

```python
except MCPError as exc:
    return f"工具执行出错：{exc}"
```

这是 S3 定下的原则：**工具出错是对话的一部分，不是对话的终点。**
模型看到错误信息往往会自己换条路。

## 进程生命周期

```python
async def close(self) -> None:
    proc = self._proc
    self._proc = None          # 先置空，保证可重复调用
    if proc is None:
        return
    if proc.returncode is None:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=5)
```

**子进程不会因为父进程的对象被回收就死掉**，它会变成孤儿继续跑。

所以 `main.py` 的 lifespan 里加了：

```python
try:
    await mcp_servers.aclose()
except Exception as exc:
    print(f"! 关闭 MCP 连接失败: {exc}")
```

不关的话，`uvicorn --reload` 改几行代码就漏一个子进程。

**这也是先修 `shell.py` 那个 `proc.kill()` bug 的原因**：
MCP 要管一个**长期存活**的子进程，模式写错会原样复制过去。

## 验证

| 检查 | 结果 |
|---|---|
| MCP 转换测试 | 11 passed |
| MCP 集成测试 | 12 passed |
| MCP 客户端测试 | 17 passed |
| 后端全套 | 121 passed（mock 122） |
| **变异测试**：不路由 MCP | 3 条失败 ✓ |
| **变异测试**：close 不 kill | 2 条失败 ✓ |
| **真机端到端** | 问"北京天气"→ 模型调用成功 ✓ |
| **无孤儿进程** | 服务停止后子进程消失 ✓ |

真机输出：

```
① 调用: mcp__weather__get_weather({"city": "北京"})
② 返回: 北京（当前）：晴，26°C，西北风 3 级
③ 回答: 北京当前晴，气温 26°C，西北风 3 级。
```

## 踩的坑：测试跑完留下子进程

`schemas()` 会懒启动 MCP 子进程，而调了它的测试没自己收尾，
跑完整套测试后系统里留着一堆 `python -m weather_server`。

两层防护：

```python
① 各测试文件加 autouse fixture —— 用完就关（正确做法）
② conftest.py 加 session 级兜底 —— 谁忘了都不会漏
```

**兜底为什么用 `pkill` 而不是优雅关闭**：优雅关闭要 await，
就得用异步的 session 级 fixture，而 `pytest-asyncio` 的 session fixture
会跨事件循环打架（`attached to a different loop`）。

**收尾不需要优雅**——测试都跑完了，杀干净就行。

另一个小坑：测试"服务器不回话"时，我一开始用 `/bin/cat`，
结果它把请求原样吐回来了，`id` 正好对得上，客户端当成响应收下了。
换成 `sleep 30`（读了 stdin 但一个字不吐）才真正测到超时。

**设计故障场景时，假的故障必须真的是故障。**

## 真实项目长什么样

多数人用框架 + 适配器：

```python
# LangChain / LangGraph 的 MCP 适配器（示意）
client = MultiServerMCPClient({
    "weather": {"command": "python", "args": ["weather_server.py"], "transport": "stdio"},
})
tools = await client.get_tools()      # 我们 convert.py 做的事就在这一行里
agent = create_react_agent(model, tools)
```

配置也从代码搬到了文件：

```json
{
  "mcpServers": {
    "weather": { "command": "python", "args": ["weather_server.py"] },
    "github":  { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] }
  }
}
```

这就是 Claude Desktop 的配置格式。我们写死在 `registry.py` 里——教学清晰，但不通用。

## 真实项目还有这些复杂度（这一步跳过了）

| 真实项目要处理 | 我们 |
|---|---|
| 并发请求多路复用（id → Future 表） | 用锁串行，够用 |
| 重连：服务器崩了自动重启 | ❌ |
| 远程传输（streamable HTTP / SSE） | ❌ 只做 stdio |
| OAuth 鉴权 | ❌ |
| 工具白名单（禁止模型调危险工具） | ❌ |
| 审计日志 | ❌ |
| resources / prompts 两个原语 | ❌ 只做 tools |
| 取消通知（`notifications/cancelled`） | ❌ |

**这些才是"麻烦"的真正来源。**
S10-4（生命周期：重连、预热、超时配置）是生产环境的事，学习项目可跳过。

## 你实际需要记住的

**不需要**：JSON-RPC 怎么分帧、id 怎么匹配、握手几步、stdout 要不要 flush。

**建议知道（会咬人的）**：

| 事项 | 为什么 |
|---|---|
| 子进程要显式关 | 不关留下孤儿进程 |
| 一个服务挂了不能拖垮全部 | 配置错一个不该让本地工具也废掉 |
| 第三方 MCP 服务是别人的代码 | `npx -y` 等于下载并运行陌生代码，有你的文件权限 |

## 一句话总结

> MCP 是一套让 AI 应用接入外部工具的通用协议。
> 工具作者写一次服务，任何支持 MCP 的应用都能直接用，不用改代码。
> 对模型来说，MCP 工具和本地工具没有区别。
