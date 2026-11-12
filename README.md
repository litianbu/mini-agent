# mini-agent

一个**从零手写**的迷你编码 agent。没有 LangChain、没有 SDK，
每一行代码都能讲清楚为什么这么写。

目标不是"造一个好用的产品"，而是**让你彻底看懂 agent 是怎么工作的** ——
然后你就能用任何语言、任何框架重写它。

> **先读 [docs/00-plan.md](docs/00-plan.md)** —— 那里讲清楚了技术栈怎么选、
> 总共要走到哪、每一步产出什么。下面只是摘要。

## 现在到哪了

| 步骤 | 内容 | 笔记 | 状态 |
|---|---|---|---|
| S1 | 流式输出：字一个个蹦出来 | [step-01](docs/step-01.md) | ✅ |
| S2 | 前后端分离（Python 后端 + React 前端） | [step-02](docs/step-02.md) | ✅ |
| S3 | 工具循环：让模型能读文件 | — | 下一步 |
| S4 | 工具集：read/write/edit/glob/grep/bash | — | |
| S5 | 会话持久化与恢复（JSONL） | — | |
| S6 | 上下文管理：裁剪与摘要 | — | |
| S7 | HTTP 加固：限流、超时、错误码 | — | |
| S8+ | 界面打磨、子 agent、MCP、评测集 | — | |

每一份 step 文档都按「要解决什么 → 核心概念 → 代码地图 → 会踩的坑 →
验收方法 → 下一步」写，**看完就跑一遍**。

## 结构

```
mini-agent/
├── apps/
│   ├── backend/     Python 3.11+ · FastAPI · httpx  ← agent 逻辑都在这
│   └── frontend/    React 19 · TypeScript · Vite    ← 只通过 HTTP 说话
└── docs/            每一步的笔记
```

两个应用平级，谁都不 import 谁，只靠一份**事件契约**耦合
（`apps/backend/mini_agent/events.py` ↔ `apps/frontend/src/types.ts`）。

## 快速开始

```bash
# 依赖
pnpm install                          # 前端
(cd apps/backend && uv sync)          # 后端（uv: brew install uv）

# 配置（不填 key 也能跑，会走本地假模型）
cp .env.example .env

# 开发：开两个终端
pnpm dev:backend                    # 后端 → http://127.0.0.1:8787
pnpm dev:frontend                    # 前端 → http://localhost:5180
```

打开 <http://localhost:5180>，输入一句话 —— 不用等整段生成完，
文字会一个个蹦出来。

不想开浏览器？命令行也行：

```bash
pnpm cli "用一句话介绍你自己"
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `pnpm dev:backend` | 起后端（带热重载） |
| `pnpm dev:frontend` | 起前端 |
| `pnpm test` | 后端测试 |
| `pnpm typecheck` | 前端类型检查 |
| `pnpm build` | 构建前端；之后 `pnpm dev:backend` 一个进程就能跑全站 |
| `pnpm cli "..."` | 命令行里跑一次对话 |

后端的 API 文档在 <http://127.0.0.1:8787/docs>（FastAPI 自动生成）。

## 设计上几个刻意的决定

- **错误也是事件**，不是异常。流一旦开始，HTTP 状态码就没法改了，
  唯一的通知渠道是流本身。
- **契约先行**。`events.py` 在写实现之前就定好了，
  所以加功能是"加一个事件类型"，不是"重构"。
- **自带假模型**。没有 API key 也能把整条管道跑通 ——
  前端开发、演示、测试都不用花钱。
- **两套传输，一个核心**。`api.py`（HTTP）和 `cli.py`（终端）
  用的是同一个 `chat()` 生成器。

## 环境要求

- Node ≥ 20 + pnpm
- Python ≥ 3.11 + [uv](https://docs.astral.sh/uv/)
- 想接真模型：`.env` 里填 `DEEPSEEK_API_KEY`（默认 `deepseek-flash`，
  也支持任何 OpenAI 兼容接口）