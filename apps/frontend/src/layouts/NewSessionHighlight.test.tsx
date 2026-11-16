/**
 * 新会话建好后，侧栏要高亮到那一条。
 *
 * 场景：在 "/" 发第一句话 → 后端分配 id=7 → 地址栏变 /sessions/7
 * → 侧栏"历史会话"下面那条应该被选中，而不是"新会话"。
 *
 * 这个用例覆盖的是一个真实 bug：地址栏变了但 react-router 不知道
 * （用的是 replaceState），侧栏就还停在"新会话"上。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import AppLayout from "./AppLayout";
import NewSessionPage from "../pages/NewSessionPage";

/** 一段最小的 SSE：start(带 session_id=7) + 一句回答 + done */
const SSE = [
  { type: "start", model: "test", mock: false, session_id: 7 },
  { type: "text", delta: "你好" },
  { type: "done", finish_reason: "stop", chars: 2, ms: 1, rounds: 1 },
]
  .map((f) => `data: ${JSON.stringify(f)}\n\n`)
  .join("");

const NEW_SESSION = {
  id: 7,
  title: "第一个问题",
  created_at: "",
  updated_at: "",
  message_count: 2,
};

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConfigProvider locale={zhCN}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<NewSessionPage />} />
              <Route path="/sessions/:id" element={<NewSessionPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

let sessionsCallCount = 0;

beforeEach(() => {
  sessionsCallCount = 0;
  vi.unstubAllGlobals();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/health")) {
        return {
          ok: true,
          json: async () => ({ ok: true, version: "x", mock: false, model: "test" }),
        } as Response;
      }
      if (u.includes("/api/chat")) {
        return {
          ok: true,
          body: {
            getReader: () => {
              let sent = false;
              return {
                read: async () => {
                  if (sent) return { done: true, value: undefined };
                  sent = true;
                  return { done: false, value: new TextEncoder().encode(SSE) };
                },
              };
            },
          },
        } as unknown as Response;
      }
      if (u.includes("/api/sessions/7")) {
        return {
          ok: true,
          json: async () => ({ ...NEW_SESSION, messages: [] }),
        } as Response;
      }
      if (u.includes("/api/sessions")) {
        // 第一次是空的（还没创建），创建后（invalidate 重取）才有 #7
        sessionsCallCount += 1;
        return {
          ok: true,
          json: async () => (sessionsCallCount === 1 ? [] : [NEW_SESSION]),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }),
  );
});

describe("新会话建好后侧栏高亮", () => {
  it("发完第一句话，高亮从「新会话」跳到那条会话", async () => {
    renderApp();

    const box = screen.getByPlaceholderText(/问点什么/);
    fireEvent.change(box, { target: { value: "第一个问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    // 注意：要限定在侧栏里查。
    // "第一个问题"在聊天区也有一份（用户消息气泡），
    // 不限定范围的话 getByText 会因为找到多个而报错。
    const sidebar = () => within(document.querySelector(".ant-menu") as HTMLElement);

    // 等侧栏出现这条会话（start 事件触发列表失效重取）
    await waitFor(() => {
      expect(sidebar().getByText("第一个问题")).toBeTruthy();
    });

    // 它必须处于选中态
    await waitFor(() => {
      const item = sidebar().getByText("第一个问题").closest(".ant-menu-item");
      expect(item?.className).toContain("ant-menu-item-selected");
    });

    // 而"新会话"不能还亮着
    const newChat = sidebar().getByText("新会话").closest(".ant-menu-item");
    expect(newChat?.className).not.toContain("ant-menu-item-selected");

    // 地址栏也应该跟着变，刷新才回得来
    expect(window.location.pathname).toBe("/sessions/7");
  });
});
