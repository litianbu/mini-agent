# S2 · 把项目拆成"真·前后端"

> 这一步没有加新功能。唯一的变化是：**后端换成 Python，并且前端和后端彻底分开。**
> 这可能比 S1 本身更重要 —— 因为从这个结构出发，后面每一步都能自然地长大。

## 一、现在的仓库长什么样

```
mini-agent/
├── apps/
│   ├── backend/              ← 后端（Python 3.11+ / FastAPI）
│   │   ├── mini_agent/
│   │   │   ├── main.py          应用入口：挂路由 + 托管前端产物
│   │   │   ├── cli.py           终端层
│   │   │   ├── core/config.py   读环境变量
│   │   │   ├── agent/           ★ 核心，与 HTTP/DB 无关
│   │   │   │   ├── events.py    契约：5 种事件
│   │   │   │   ├── delta.py     模型增量
│   │   │   │   ├── loop.py      agent 核心：产出事件流
│   │   │   │   └── llm/         client / sse / mock
│   │   │   ├── api/             HTTP 层
│   │   │   │   ├── deps.py      依赖注入（S5 加 get_db）
│   │   │   │   └── routes/      chat / health
│   │   │   ├── schemas/         请求响应模型
│   │   │   ├── db/              ⏳ S5：数据库
│   │   │   └── services/        ⏳ S5：业务编排
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── uv.lock
│   └── frontend/             ← 前端（React 19 + TypeScript + Vite）
│       ├── src/
│       │   ├── types.ts        契约的 TS 镜像
│       │   ├── api/            index（SSE 解析）+ client（React Query）
│       │   ├── hooks/          useChatStream（流式状态机）
│       │   ├── App.tsx         路由表
│       │   ├── layouts/        布局（左侧菜单 + Outlet）
│       │   └── pages/          页面（新会话 / 历史会话）
│       └── vite.config.ts
├── docs/                     ← 每一步的笔记（你正在读的）
├── package.json              ← 只管前端那点 Node 脚本
└── pnpm-workspace.yaml
```

`apps/` 下两个目录平级，谁都不 import 谁 —— 它们**只通过 HTTP 上的 JSON 说话**。
这是"前后端分离"的真正含义，不只是放在两个文件夹里。

> 为什么叫 `backend` / `frontend` 而不是 `api` / `web`？
> 因为 `api` 只描述了它对外暴露的那一层，而里面放着整个 agent 核心。
> 名字说清楚"这是什么角色"，比说清楚"它怎么被访问"更有用。

## 二、为什么要这么分

**1. 语言该用在哪就用在哪。**
前端生态（Vite / React / TS）是 Node 的世界，离不开；
而真实项目里服务端几乎都是 Python / Java / Go —— Python 有 FastAPI、有 sqlalchemy、
有整个 AI 生态。硬用一种语言统一两端，是学生项目的做法。

**2. 依赖是隔离的。**
前端 `node_modules`（700MB）和后端 `.venv`（30MB）互不干扰，
构建前端不会碰到 Python 依赖，反之亦然。部署时也可以分开：
前端丢 CDN，后端跑容器。

**3. 契约成了唯一耦合点。**
`events.py` 里 5 个 Pydantic 模型就是全部约定。
前端改样式、后端加内部逻辑，互不影响；只有契约变了才需要同步 ——
而这一点被 `types.ts` 和类型检查盯着。

## 三、契约长什么样

一条流就是 5 种事件，`type` 字段区分：

| 事件 | 什么时候发 | 前端干什么 |
|---|---|---|
| `start` | 流开始 | 显示模型名 / MOCK 标识 |
| `reasoning` | 模型在"想" | 灰色小字显示 |
| `text` | 正文增量 | 拼接到正文 |
| `done` | 正常结束 | 显示耗时、字数 |
| `error` | 出错 | 红色提示（**注意：错误也是事件，不是抛异常**） |

为什么把错误也做成事件？因为流已经开始了，HTTP 状态码早就发出去了
（200），这时候唯一的通知渠道就是流本身。

## 四、怎么跑

```bash
cd mini-agent

# 一次性安装
pnpm install                 # 前端依赖
cd apps/backend && uv sync       # 后端依赖（需要先装 uv：brew install uv）
cd ../..

cp .env.example .env         # 不填 key 也能跑，会走 MOCK

# 开两个终端
pnpm dev:backend                 # http://127.0.0.1:8787
pnpm dev:frontend                 # http://localhost:5180
```

前端为什么是 5180？Vite 默认 5173，但端口可以改；
`vite.config.ts` 里把 `/api` 代理到 8787，浏览器眼里就是同源 ——
**所以后端完全不用配 CORS**。这是本地开发最省心的做法。

## 五、生产模式（一个进程跑全站）

```bash
pnpm build                   # 产出 apps/frontend/dist
pnpm dev:backend                 # 然后直接访问 :8787
```

`api.py` 末尾做了两件事：把 `dist/assets` 挂成静态目录，
再加一条 `/{full_path:path}` 兜底路由，把 `/`、`/sessions` 这类地址
统一返回 `index.html`（没 build 过就返回 503）。

**为什么需要兜底？** 因为前端用的是 BrowserRouter（真实路径、没有 `#` 号）。
你在浏览器里刷新 `/sessions`，这个请求会真的打到后端 —— 后端并没有这个文件，
不兜底就是 404。兜底之后，`index.html` 交给前端路由，前端自己决定渲染哪页。

这条兜底路由必须放在**最后**，并且要放过 `/api/` 前缀（否则打错的 API
会返回一个 HTML，前端解析 JSON 时崩掉）。

## 五点五、前端路由与布局

```
src/
├── App.tsx              路由表（Routes + Route）
├── layouts/AppLayout.tsx 外壳：左侧菜单 + <Outlet />
└── pages/
    ├── NewSessionPage.tsx   "/"        新会话（聊天界面）
    └── SessionsPage.tsx     "/sessions" 历史会话（占位，S5 填）
```

关键概念是**布局路由**：`<Route element={<AppLayout />}>` 这条父路由不带
`path`，只负责外壳；子路由渲染在 `<Outlet />` 那个位置。
好处是切页面时侧边栏**不会重建** —— 不用每个页面各写一遍菜单。

`NavLink` 的 `end` 属性只给 `"/"` 加：不加的话，`/sessions` 也会被算作
`"/"` 的命中，两个菜单项会同时高亮。

## 五点六、UI 交给 antd

界面已经换成 **Ant Design 6** 的组件，`styles.css` 只剩 30 行（整页高度 +
品牌标识）。换来的是暗色主题、中文字体、空状态、404 页全都不用自己画。

三个关键点：

1. **`ConfigProvider` 是全局开关**，在 `main.tsx` 里包住整个应用：

   ```tsx
   <ConfigProvider locale={zhCN} theme={{ algorithm: theme.darkAlgorithm }}>
   ```

   `locale` 让分页、空状态这些内置文案变中文；`darkAlgorithm` 一行开暗色。
   **颜色不要散在组件里写**，否则换主题时要改到崩溃。

2. **antd 是 CSS-in-JS，不用引样式文件**（除了 `reset.css` 打底）。
   在 `main.tsx` 里引一次 `antd/dist/reset.css` 就够，其余由 token 生成。

3. **体积要分包。** 引入 antd 后单包从 227KB 涨到 811KB，Vite 会警告。
   在 `vite.config.ts` 里按依赖拆成三块：

   | 包 | gzip | 什么时候变 |
   |---|---|---|
   | 业务代码 | 6KB | 每次改代码 |
   | react + react-router | 18KB | 极少 |
   | antd | 240KB | 极少 |

   这样改业务代码时，浏览器里缓存的 antd 依然有效 ——
   否则用户每次刷新都要重下 240KB。中后台项目基本都这么拆。

## 六、两个坑，顺便记住

1. **`fetch` 的坑**：`EventSource` 只能发 GET、不能带 body、不能 abort。
   所以前端是手写的 `fetch` + `ReadableStream` 解析（`api.ts` 里 30 行）。
   别小看这 30 行 —— 它决定了"停止"按钮能不能真的停下来。

2. **跨解释器/跨服务的坑**：Python 端 `finish_reason` 是蛇形命名，
   前端第一次写成 `finishReason` 就被 `tsc` 抓出来了。
   契约字段一旦定下就不要随手改风格，两端都得照着来。

## 七、验收清单

- [ ] `pnpm dev:backend` 后打开 <http://127.0.0.1:8787/docs> 能看到自动生成的 API 文档
- [ ] `curl localhost:8787/api/health` 返回 `{"ok":true,...}`
- [ ] 浏览器里输入问题，文字**逐字**出现
- [ ] 界面是 antd 风格：暗色底、左侧深色菜单、按钮/输入框带圆角
- [ ] 点左侧菜单「历史会话」，地址变成 /sessions，侧边栏高亮跟着变
- [ ] 在 /sessions 上刷新页面，不出现 404（SPA 兜底生效）
- [ ] 点"停止"，流立刻断（后端日志里能看到连接关闭）
- [ ] `pnpm test` 三个测试全绿
- [ ] `pnpm typecheck` 无报错

## 八、下一步

**S3 工具循环**：让模型能说"我要读文件"，程序替它读，把结果塞回去。
那 20 行是整个 agent 的心脏 —— 而现在的地基（事件流 + 契约 + 分层）
已经足够承载它了，`api.py` 到时候一行都不用改。