/**
 * 库里的消息 → 界面气泡。
 *
 * 落库之后，一轮工具调用在库里是三条记录：
 *   assistant（声明调用，content 为空）
 *   tool（结果，靠 tool_call_id 配对）
 *   assistant（最终回答）
 *
 * 界面上要还原成一个气泡、里面挂着工具步骤 —— 不是三条独立消息。
 */
import { describe, expect, it } from "vitest";
import { toBubbles } from "./NewSessionPage";
import type { StoredMessage } from "../types";

function msg(over: Partial<StoredMessage>): StoredMessage {
  return {
    id: 1,
    role: "assistant",
    content: "",
    reasoning: null,
    tool_calls: null,
    tool_call_id: null,
    created_at: "",
    ...over,
  };
}

const CALLS = JSON.stringify([
  { id: "call_a", type: "function", function: { name: "bash", arguments: '{"cmd":"ls"}' } },
]);

describe("toBubbles", () => {
  it("一轮工具调用还原成一个气泡 + 一个工具步骤", () => {
    const bubbles = toBubbles([
      msg({ id: 1, role: "user", content: "数一下文件" }),
      msg({ id: 2, tool_calls: CALLS }),
      msg({ id: 3, role: "tool", content: "退出码 0\n5", tool_call_id: "call_a" }),
      msg({ id: 4, content: "共 5 个" }),
    ]);

    // 三条库记录 → 两个气泡（用户 + 助手），tool 那条并进去了
    expect(bubbles).toHaveLength(2);

    const bot = bubbles[1]!;
    expect(bot.text).toBe("共 5 个");
    expect(bot.tools).toHaveLength(1);
    expect(bot.tools[0]!.name).toBe("bash");
    expect(bot.tools[0]!.output).toBe("退出码 0\n5");
    expect(bot.tools[0]!.done).toBe(true);
  });

  it("工具结果缺失时，步骤显示为未完成而不是消失", () => {
    const bubbles = toBubbles([
      msg({ id: 1, role: "user", content: "问" }),
      msg({ id: 2, tool_calls: CALLS }),
    ]);

    expect(bubbles[1]!.tools[0]!.done).toBe(false);
    expect(bubbles[1]!.tools[0]!.output).toBe("");
  });

  it("tool_calls 是坏 JSON 时不崩，只是没有工具步骤", () => {
    const bubbles = toBubbles([
      msg({ id: 1, role: "user", content: "问" }),
      msg({ id: 2, content: "回答", tool_calls: "{不是 JSON" }),
    ]);

    expect(bubbles[1]!.text).toBe("回答");
    expect(bubbles[1]!.tools).toHaveLength(0);
  });

  it("空内容的 assistant（纯工具声明）不会变成空气泡", () => {
    const bubbles = toBubbles([
      msg({ id: 1, role: "user", content: "问" }),
      msg({ id: 2, tool_calls: null, content: "" }),
    ]);

    expect(bubbles).toHaveLength(1); // 只剩用户那条
  });

  it("多个工具并行调用都要还原", () => {
    const two = JSON.stringify([
      { id: "a", type: "function", function: { name: "read_file", arguments: "{}" } },
      { id: "b", type: "function", function: { name: "bash", arguments: "{}" } },
    ]);
    const bubbles = toBubbles([
      msg({ id: 1, role: "user", content: "问" }),
      msg({ id: 2, tool_calls: two }),
      msg({ id: 3, role: "tool", content: "A", tool_call_id: "a" }),
      msg({ id: 4, role: "tool", content: "B", tool_call_id: "b" }),
    ]);

    expect(bubbles[1]!.tools.map((t) => t.name)).toEqual(["read_file", "bash"]);
    expect(bubbles[1]!.tools.map((t) => t.output)).toEqual(["A", "B"]);
  });
});
