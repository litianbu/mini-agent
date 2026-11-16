/**
 * 侧栏高亮归属。
 *
 * 这个逻辑错过一次：在 /sessions/20 里左边高亮的是"新会话"。
 * 原因是只做了路径相等比较，详情页匹配不上就掉进了兜底。
 *
 * 现在会话列表直接列在侧栏，所以还要能高亮到**具体那一条**。
 */
import { describe, expect, it } from "vitest";
import { activeNavKey, sessionIdFromPath } from "./AppLayout";

describe("sessionIdFromPath", () => {
  it("从详情路径里取出 id", () => {
    expect(sessionIdFromPath("/sessions/20")).toBe(20);
    expect(sessionIdFromPath("/sessions/20/")).toBe(20);
  });

  it("不是详情路径就返回 null", () => {
    expect(sessionIdFromPath("/sessions")).toBeNull();
    expect(sessionIdFromPath("/")).toBeNull();
    expect(sessionIdFromPath("/sessions/abc")).toBeNull();
  });
});

describe("activeNavKey", () => {
  it("根路径 → 新会话", () => {
    expect(activeNavKey("/")).toBe("/");
  });

  it("会话列表 → 全部会话", () => {
    expect(activeNavKey("/sessions")).toBe("/sessions");
  });

  it("会话详情 → 高亮到具体那一条（侧栏子项）", () => {
    expect(activeNavKey("/sessions/20")).toBe("/sessions/20");
    expect(activeNavKey("/sessions/999")).toBe("/sessions/999");
  });

  it("未知路径回退到新会话", () => {
    expect(activeNavKey("/whatever")).toBe("/");
    // 前缀必须按路径段：/sessions-archive 不是 /sessions 的子路径
    expect(activeNavKey("/sessions-archive")).toBe("/");
  });
});
