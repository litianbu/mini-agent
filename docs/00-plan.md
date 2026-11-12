# 总体规划

> 这份文档回答三个问题：**做什么、用什么做、按什么顺序做。**
> 后面每一步的笔记（`step-0X.md`）都是它的展开。

## 一、目标与非目标

**目标**

1. 从零手写一个能读写文件、执行命令的编码 agent，每一行都能讲清楚为什么。
2. 代码量控制在"能一口气读完"的范围内：每步新增 ≤ 250 行。
3. 每一层都可以替换：换模型、换 UI、换存储，不用重写其他部分。
4. 不用 API key 也能跑通全链路（内置假模型），开发和演示不花钱。

**非目标**（明确不做，避免范围膨胀）

- 不追求生产级性能、多租户、权限体系。
- 不引入 agent 框架（LangChain / LlamaIndex）——它们会把你要看的机制遮住。
- 不做权限审批 UI（先留 TODO，等核心跑通再说）。

## 二、技术栈

| 层 | 选型 | 为什么是它 | 考虑过的替代 |
|---|---|---|---|
| 后端语言 | **Python 3.11+** | 服务端主流；AI 生态就在这门语言里 | Node/TS 也能写，但没必要用一个语言包办两端 |
| Web 框架 | **FastAPI** | 类型即文档，`/docs` 自动生成；天生支持流式响应 | Flask（异步流要自己搭）、Django（对本项目太重） |
| ASGI 服务器 | **uvicorn** | FastAPI 的官方搭配，自带热重载 | hypercorn |
| HTTP 客户端 | **httpx** | 同时支持 sync/async，支持流式读取 | requests（不支持 async 和流式） |
| 配置 | **pydantic-settings** | 环境变量 + `.env` 一处收口，还能做类型校验 | 手写 `os.getenv`（会散落各处） |
| 依赖管理 | **uv** | 装得快；`uv.lock` 保证可复现 | pip + requirements.txt、poetry |
| 前端 | **React 19 + TypeScript** | 生态最大；类型系统能把契约错误挡在编译期 | Vue（也完全够用）、原生 JS（契约靠人脑记） |
| UI 组件库 | **Ant Design 6** | 后台界面开箱即用，中文语境最贴；暗色主题一行配置就能开 | 手写 CSS（本项目先写过一版，能用但费时间）、Tailwind（还要自己搭组件）、MUI（Material 风格） |
| 前端路由 | **React Router 7** | 事实标准；布局路由让侧边栏只渲染一次 | TanStack Router（类型更强，但更重） |
| 数据请求 | **TanStack Query 5** | 缓存、去重、失效、加载态开箱即用；S5 的会话列表正好用它 | SWR（更轻但生态小）、RTK Query（要引入 Redux） |
| 数据库 | **SQLAlchemy 2.0 + Alembic**（S5） | 主流、类型友好、换数据库只改 URL | 裸驱动手写 SQL（易出事故）、Django ORM（非 Django 项目） |
| 构建工具 | **Vite** | 启动毫秒级，热更新顺手 | Next.js（本项目不需要 SSR 和路由） |
| 包管理 | **pnpm workspace** | 单仓多包；快、省磁盘 | npm / yarn |
| 传输协议 | **SSE**（`text/event-stream`） | 单向、纯 HTTP、无需额外协议，正好匹配"边说边发" | WebSocket（双向能力用不上，还多一套握手/心跳） |
| 数据格式 | **JSON 事件** | 人能读、浏览器能直接解析、日志能直接 grep | 二进制/压缩协议（过早优化） |

**一个刻意的取舍：** 前端用 TS，后端用 Python，跨语言的代价是契约要手动同步
（`events.py` ↔ `types.ts`）。这是真实项目里的常态，也是本项目的教学点之一 ——
我们会用"契约先行 + 类型检查"来控住它。

## 三、仓库结构

```
mini-agent/
├── apps/
│   ├── backend/                  Python · FastAPI · uv
│   │   └── mini_agent/
│   │       ├── main.py           应用入口：组装路由 + 托管前端产物
│   │       ├── cli.py            终端入口（同一份事件流，换个出口）
│   │       ├── core/             基础设施：config / database
│   │       ├── agent/            ★ 核心，与 HTTP/DB 无关
│   │       │   ├── events.py     事件契约（5 种事件 + to_sse）
│   │       │   ├── delta.py      模型增量的形状
│   │       │   ├── loop.py       心脏：Delta → 事件
│   │       │   └── llm/          client（发请求）/ sse（分帧）/ mock（假模型）
│   │       ├── api/              HTTP 层
│   │       │   ├── deps.py       依赖注入（S5 加 get_db）
│   │       │   └── routes/       chat / health
│   │       ├── schemas/          请求响应模型（与 events 分开）
│   │       ├── models/           表定义（Base / ChatSession / Message）
│   │       └── services/         ⏳ S5：业务编排
│   └── frontend/                 React · TypeScript · Vite
│       └── src/
│           ├── main.tsx          挂载：Router + QueryClient + antd ConfigProvider
│           ├── App.tsx           路由表
│           ├── layouts/          外壳（侧栏只渲染一次）
│           ├── pages/            页面
│           ├── hooks/            useChatStream（流式状态机）
│           ├── api/              index（SSE 解析）+ client（React Query hooks）
│           └── types.ts          手抄后端的契约
├── docs/                         规划 + 每一步的笔记
├── package.json                  根脚本
├── pnpm-workspace.yaml
└── README.md
```

`apps/` 下两个目录平级，互不 import，**只通过 HTTP 上的 JSON 说话**。

### 为什么后端要这么多层

一句话概括每层的"不知道"：

| 层 | 知道什么 | **不知道**什么 |
|---|---|---|
| `api/` | HTTP、请求体、连接断开 | 模型怎么调用 |
| `agent/` | 事件、循环、工具 | HTTP、数据库、前端 |
| `llm/` | 请求格式、SSE 分帧 | 谁在调用它 |
| `models/` | 表结构 | 怎么连库、业务规则 |
| `services/` | 业务流程（先存再调） | 具体怎么存、怎么发 |

**"不知道什么"比"知道什么"更能说明一个模块的边界。**
分层不是为了好看，是为了改一处时不牵动别处。

## 三点五、数据库（连接层已接通）

MySQL 9.7.2 · utf8mb4 · 库名 `mini_agent`

### 目录怎么分的

```
mini_agent/
├── models/
│   └── __init__.py    ← 表长什么样（Base / ChatSession / Message）
└── core/
    ├── config.py      ← 配置（含数据库地址、端口、库名）
    └── database.py    ← 怎么连上（engine / SessionLocal / get_db / 建表）
```

**为什么叫 `models` 不叫 `db`**：
`db` 太笼统 —— 看名字不知道里面是表结构还是连接代码。
`models` 是这个概念的标准叫法（Django、Rails、FastAPI 教程都这么叫），
看到目录名就知道"表定义在这里"。连接代码属于基础设施，归 `core/`。

想加表 → 改 `models/__init__.py`
想改连接池 → 改 `core/database.py`
想知道密码从哪来 → 改 `core/config.py`（密码本身在 `.env`）

### 配置全部收口在 config.py

```python
db_driver: str = "mysql+aiomysql"   # 公共配置，写在源码里
db_host: str   = "127.0.0.1"
db_port: int   = 3306
db_user: str   = "root"
db_name: str   = "mini_agent"
db_password: str = ""               # ← 只在 .env 里出现，源码永远是空
```

所以 `.env` 里只有一行 `DB_PASSWORD=...`。
**公共的进 config.py，机密的进 .env** —— 这条线划清楚，
就不会有人把密码写进源码，也不会有人为了改个端口去翻文档。

要接别的库，只改 `db_driver`：
`mysql+aiomysql` / `postgresql+asyncpg` / `sqlite+aiosqlite`

### 两条命令

```bash
cd apps/backend && uv run python -m mini_agent.core.database   # 建表
curl localhost:8787/api/health                                 # 看 db 字段
```

### 关键设计决定

| 决定 | 为什么 |
|---|---|
| **异步驱动** `aiomysql` | 后端整个是 async；同步驱动会阻塞事件循环，流式接口最受伤 |
| **一个 engine**，不是每请求一个 | engine 内部有连接池，反复新建等于反复握手，几十个请求就打满连接数 |
| **`pool_pre_ping=True`** | MySQL 会悄悄断开闲置连接（wait_timeout），不 ping 首次查询就报 `server has gone away` |
| **`expire_on_commit=False`** | 默认值会在 commit 后让属性失效，async 里触发 `MissingGreenlet` |
| **`longtext` 而非 `text`** | MySQL 的 TEXT 上限 64KB，长回答会爆 |
| **表类叫 `ChatSession`** | SQLAlchemy 已有 `Session`（连接会话）。两个"会话"撞名最坑人 |
| **密码 `quote_plus` 转义** | 密码里有 `@ / #` 会直接把 URL 拆坏 |
| **没配密码就 `disabled`** | 和 MOCK 同一思路：默认能跑，想要完整能力再配 |

### ⚠️ 两件待办

1. **别长期用 `root`**。建个专用账号只给 `mini_agent` 库权限：
   ```sql
   CREATE USER 'mini_agent'@'localhost' IDENTIFIED BY '...';
   GRANT ALL PRIVILEGES ON mini_agent.* TO 'mini_agent'@'localhost';
   ```
2. **改表结构时 `create_all` 不管用**。它只建缺失的表，不会给已存在的表加列。
   第一次要改列时，就该让 alembic 接手。

## 四、唯一的耦合点：事件契约

后端 `apps/backend/mini_agent/events.py` 定义 5 种事件，
前端 `apps/frontend/src/types.ts` 是它的手写镜像：

| 事件 | 时机 | 前端表现 |
|---|---|---|
| `start` | 流开始 | 显示模型名 / MOCK 标识 |
| `reasoning` | 模型思考中 | 灰色小字 |
| `text` | 正文增量 | 追加到正文 |
| `done` | 正常结束 | 显示耗时、字数 |
| `error` | 出错 | 红色提示 |

**规则：契约只增不改。** 加功能 = 加一种事件，而不是改已有字段。
这样前端可以渐进增强，老代码不会突然坏掉。

## 五、路线图

| 步骤 | 主题 | 你会学到 | 预计 | 状态 |
|---|---|---|---|---|
| S1 | 流式输出 | `stream: true`、SSE 分帧、增量渲染 | 半天 | ✅ |
| S2 | 前后端分离 | 分层、契约、代理、Monorepo | 半天 | ✅ |
| S3 | 工具循环 | `tool_calls` 累积、`tool_call_id` 配对、循环刹车 | 半天 | ✅ 完成 |
| S4 | 工具集扩充 | edit 精确替换、grep 内容搜索、路径越界防护 | 1 天 | ✅ 完成 |
| S5 | 会话持久化 | 存进 MySQL、恢复会话、会话列表 | 半天 | 下一步 |
| S6 | 上下文管理 | 裁剪 vs 摘要、token 估算 | 半天 | |
| S7 | HTTP 加固 | 超时、限流、错误码、取消传播 | 半天 | |
| S8 | 界面打磨 | 工具调用可视化、Markdown 渲染 | 1 天 | |
| S9 | 子 agent + 多模型 | 任务委派、模型路由 | 1 天 | |
| S10 | MCP | stdio 协议、外部工具接入 | 1 天 | |
| S11 | 评测集 | 10 个固定用例、回归验证 | 半天 | |

合计 7 天左右（按每天投入 2–3 小时算，实际会更快 —— 越往后越顺手）。

**S1 和 S2 是地基**：没有流式，agent 的工具调用过程会像卡死；
没有分层和契约，后面每加一个功能都要改动所有文件。

## 六、开发约定

1. **契约先行。** 先想清楚"前端需要什么事件"，再写实现。改契约要同时改两侧。
2. **每步可运行。** 任何一步结束时，`pnpm dev:backend` + `pnpm dev:frontend` 必须是通的。
3. **一步一个概念。** 不在一格里同时塞流式 + 工具 + 存储。
4. **注释写"为什么"，不写"是什么"。** `i += 1  # 加一` 是噪音；
   "这里必须 flush，否则代理会攒一批再发" 才是信息。
5. **默认能离线跑。** 没有 API key 自动进 MOCK 模式，整条管道照常工作。
6. **命名：** Python 用 `snake_case`，TS 用 `camelCase`，
   但**跨网络的 JSON 字段以服务端为准**（所以是 `finish_reason`，不是 `finishReason`）。
7. **每步结束由你验收。** 我改完 → 你亲自跑一遍 → 确认没问题再进入下一步。

## 七、怎么读这些文档

按顺序读，`step-0X.md` 与路线图的步骤号一一对应。
每份 step 文档结构固定：

```
要解决什么问题 → 核心概念 → 代码地图 → 会踩的坑 → 验收方法 → 下一步
```

遇到看不懂的，先看那份文档的"核心概念"，再回头看代码 —— 顺序反了会很痛苦。

## 八、现在在哪

S1～S4 已完成并验证：

- 前端 <http://localhost:5180>
- 后端 <http://127.0.0.1:8787>，API 文档在 `/docs`
- 数据库 `mini_agent`（建表：`uv run python -m app.core.database`）
- 6 个工具：`glob` / `grep` / `read_file` / `edit` / `write_file` / `bash`

**现在它是个能干活儿的 agent 了**：会按内容搜代码、精准改几行、
跑测试验证自己改得对不对。`app/agent/loop.py` 那个 while 就是 ReAct 的全部。

界面也像样了：消息分左右气泡、助手回答走 Markdown（表格/代码块都正常）、
工具调用可折叠成一行 `工具调用 2 个 · 全部成功`。

**下一步是 S5 会话持久化** —— 现在刷新页面，对话就没了。
`models/` 里那两张表（`sessions` / `messages`）已经建好，但还没人往里写。
S5 要做的是：每轮对话存库、侧栏列出历史会话、点进去能接着聊。
