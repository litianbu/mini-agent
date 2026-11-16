/**
 * 聊天页的两条回归测试。
 *
 * 都是真实踩过的 bug，都属于"渲染测试测不出来"的那一类：
 *   ① 从历史会话进去空白（数据晚一步到，useState 初始值不会更新）
 *   ② 新会话发完消息后刷新，内容丢失（地址栏没跟着变）
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import NewSessionPage from "./NewSessionPage";

function renderAt(path: string) {
  const qc = new QueryClient({
    // 测试里不要重试，失败就立刻失败
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ConfigProvider locale={zhCN}>
        <MemoryRouter initialEntries={[path]}>
          {/* 必须有真实路由：useParams() 靠 Route 提供的 :id 才能拿到值。
              直接渲染组件的话 params 是空的，测出来的是"新会话"分支。 */}
          <Routes>
            <Route path="/" element={<NewSessionPage />} />
            <Route path="/sessions/:id" element={<NewSessionPage />} />
          </Routes>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

/** 造一个假后端：会话详情返回两条消息。 */
function mockSessionApi(delayMs = 0) {
  const detail = {
    id: 1,
    title: "历史会话标题",
    messages: [
      { id: 1, role: "user", content: "第一个问题", reasoning: null, created_at: "" },
      { id: 2, role: "assistant", content: "第一个回答", reasoning: null, created_at: "" },
    ],
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
      if (String(url).includes("/api/sessions/1")) {
        return { ok: true, json: async () => detail } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }),
  );
  return detail;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("打开历史会话", () => {
  it("接口数据晚一步到达时，消息仍然要渲染出来", async () => {
    // 关键就在这个延迟：数据不是首帧就有的。
    // 修复前，useState 的初始值是空数组，数据到了也不会更新 → 页面空白。
    mockSessionApi(30);
    renderAt("/sessions/1");

    // 先看到加载态（说明确实经历了"还没数据"的阶段）
    expect(await screen.findByText(/正在读取会话|历史会话标题/)).toBeTruthy();

    // 数据到了之后，两条消息都必须出现在页面上
    await waitFor(() => {
      expect(screen.getByText("第一个问题")).toBeTruthy();
      expect(screen.getByText("第一个回答")).toBeTruthy();
    });
  });

  it("标题也要显示，不能一直是「加载中」", async () => {
    mockSessionApi(20);
    renderAt("/sessions/1");

    await waitFor(() => {
      expect(screen.getByText("历史会话标题")).toBeTruthy();
    });
  });

  it("会话不存在时给提示，而不是白屏", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as Response),
    );
    renderAt("/sessions/999");

    await waitFor(() => {
      expect(screen.getByText(/这个会话读不出来/)).toBeTruthy();
    });
  });
});

describe("新会话", () => {
  it("首屏没有会话时直接给输入框，不要卡在加载态", () => {
    mockSessionApi();
    renderAt("/");
    expect(screen.getByPlaceholderText(/问点什么/)).toBeTruthy();
  });
});

describe("新会话拿到 id 后要改地址栏", () => {
  it("发完第一条消息，URL 变成 /sessions/N（否则刷新就丢了）", async () => {
    // 假后端：/api/chat 返回一条最小 SSE 流（start 带 session_id + 一段文字 + done）
    const frames = [
      { type: "start", model: "test-model", mock: false, session_id: 7 },
      { type: "text", delta: "你好" },
      {
        type: "done",
        finish_reason: "stop",
        chars: 2,
        ms: 1,
        rounds: 1,
      },
    ];
    const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/chat")) {
          return {
            ok: true,
            body: {
              getReader: () => {
                let sent = false;
                return {
                  read: async () => {
                    if (sent) return { done: true, value: undefined };
                    sent = true;
                    return { done: false, value: new TextEncoder().encode(body) };
                  },
                };
              },
            },
          } as unknown as Response;
        }
        return { ok: true, json: async () => [] } as Response;
      }),
    );

    renderAt("/");

    const box = screen.getByPlaceholderText(/问点什么/);
    fireEvent.change(box, { target: { value: "第一个问题" } });
    // 按 role 找按钮，比 getByText 稳：antd 会在文字外面套一层 span
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    // 收到 start 事件后，地址栏必须被换成 /sessions/7
    await waitFor(() => {
      expect(window.location.pathname).toBe("/sessions/7");
    });
  });
});
