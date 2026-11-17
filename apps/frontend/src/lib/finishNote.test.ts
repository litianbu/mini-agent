/**
 * 结束提示的分工：正常时不打扰，出问题才说话。
 *
 * 起因：界面上挂过这么一串
 *   tool_calls · 0 字 · 32718ms · 8 轮 · 53644 token（入 51197 / 出 2447）
 * 这是开发者要看的东西，用户看不明白也没必要看。
 */
import { describe, expect, it } from "vitest";
import { debugNote, finishNote, metaLine } from "./finishNote";
import type { DoneEvent } from "../types";

function done(over: Partial<DoneEvent> = {}): DoneEvent {
  return {
    type: "done",
    finish_reason: "stop",
    ms: 1200,
    chars: 78,
    rounds: 1,
    prompt_tokens: 1342,
    completion_tokens: 75,
    total_tokens: 1417,
    ...over,
  };
}

describe("给用户看的提示", () => {
  it("正常结束什么都不显示", () => {
    expect(finishNote(done())).toBeNull();
  });

  it("回答被截断要说清楚", () => {
    expect(finishNote(done({ finish_reason: "length" }))).toBe("回答太长，被截断了");
  });

  it("轮数用完不再补一句 —— 后端已经发过 ErrorEvent 了", () => {
    expect(finishNote(done({ finish_reason: "tool_calls" }))).toBeNull();
  });

  it("没见过的结束原因，把原始值给出来而不是装没事", () => {
    expect(finishNote(done({ finish_reason: "weird_thing" }))).toContain("weird_thing");
  });
});

describe("给开发看的调试信息", () => {
  it("带轮数、耗时、token", () => {
    const d = debugNote(done());
    expect(d).toContain("1 轮");
    expect(d).toContain("1.2s");
    expect(d).toContain("1417 token");
    expect(d).toContain("入 1342");
  });

  it("token 为 0（mock 模式）时不显示，免得误导", () => {
    const d = debugNote(done({ total_tokens: 0, prompt_tokens: 0, completion_tokens: 0 }));
    expect(d).not.toContain("token");
  });
});

describe("两路合起来", () => {
  it("生产环境正常结束：整行是空的（界面就不显示）", () => {
    expect(metaLine(done(), false)).toBe("");
  });

  it("生产环境出问题：只显示人话，不带技术细节", () => {
    const line = metaLine(done({ finish_reason: "length" }), false);
    expect(line).toBe("回答太长，被截断了");
    expect(line).not.toContain("token");
    expect(line).not.toContain("轮");
  });

  it("开发环境：调试信息始终可见", () => {
    const line = metaLine(done(), true);
    expect(line).toContain("finish=stop");
    expect(line).toContain("1417 token");
  });
});
