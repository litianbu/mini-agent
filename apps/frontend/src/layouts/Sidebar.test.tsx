/**
 * 侧栏的会话列表。
 *
 * 验证的是"用户看到的样子"：历史会话展开后，
 * 每条会话都在菜单里，并且在对应页面上是高亮的。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import AppLayout from "./AppLayout";

const SESSIONS = [
  { id: 20, title: "测试第一条数据", created_at: "", updated_at: "", message_count: 4 },
  { id: 21, title: "第二个会话", created_at: "", updated_at: "", message_count: 2 },
];

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConfigProvider locale={zhCN}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<div>新会话页</div>} />
              <Route path="/sessions" element={<div>列表页</div>} />
              <Route path="/sessions/:id" element={<div>聊天页</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("/api/sessions")) {
        return { ok: true, json: async () => SESSIONS } as Response;
      }
      // health 接口
      return {
        ok: true,
        json: async () => ({ ok: true, version: "x", mock: false, model: "test" }),
      } as Response;
    }),
  );
});

describe("侧栏会话列表", () => {
  it("历史会话下面列出每条会话", async () => {
    renderAt("/");
    await waitFor(() => {
      expect(screen.getByText("测试第一条数据")).toBeTruthy();
      expect(screen.getByText("第二个会话")).toBeTruthy();
    });
    // 还要有回列表页的入口
    expect(screen.getByText("全部会话")).toBeTruthy();
  });

  it("进入某个会话时，那一条被标记为选中", async () => {
    renderAt("/sessions/20");

    await waitFor(() => {
      expect(screen.getByText("测试第一条数据")).toBeTruthy();
    });

    // antd 给选中项加 .ant-menu-item-selected
    const item = screen.getByText("测试第一条数据").closest(".ant-menu-item");
    expect(item?.className).toContain("ant-menu-item-selected");
    // 而且不能同时把"新会话"也标成选中
    const newChat = screen.getByText("新会话").closest(".ant-menu-item");
    expect(newChat?.className).not.toContain("ant-menu-item-selected");
  });

  it("没有会话时给一句提示，而不是空着", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] }) as Response),
    );
    renderAt("/");
    await waitFor(() => {
      expect(screen.getByText("还没有会话")).toBeTruthy();
    });
  });
});
