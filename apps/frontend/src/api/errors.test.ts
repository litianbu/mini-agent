/**
 * 后端错误 → 一句人话。
 *
 * 起因：用户粘了一段长文本，界面弹出
 *   网络错误：HTTP 422 {"detail":[{"type":"string_too_long",...
 *     "input":"# 任务：审查一段..."}]}
 * 英文、内部字段、还回显了输入内容，而且"网络错误"这个说法本身就是错的。
 */
import { describe, expect, it } from "vitest";
import { errorMessage } from "./errors";

describe("errorMessage", () => {
  it("HTTPException 的 detail 直接用", () => {
    expect(errorMessage(429, JSON.stringify({ detail: "上一条还在生成，请稍候" }))).toBe(
      "上一条还在生成，请稍候",
    );
  });

  it("入参校验失败：说出哪个字段、什么毛病", () => {
    const body = JSON.stringify({
      detail: [
        {
          type: "string_too_long",
          loc: ["body", "question"],
          msg: "String should have at most 8000 characters",
          input: "# 任务：审查一段...",
        },
      ],
    });
    const msg = errorMessage(422, body);

    expect(msg).toContain("question");
    expect(msg).toContain("8000");
    // 关键：不要把用户的输入原样回显
    expect(msg).not.toContain("# 任务：审查一段");
  });

  it("响应体不是 JSON（比如网关的 HTML 错误页）也不能崩", () => {
    expect(errorMessage(502, "<html>Bad Gateway</html>")).toBe("请求失败（502）");
  });

  it("空响应体也要有兜底话术", () => {
    expect(errorMessage(500, "")).toBe("服务端出错了");
  });

  it("常见状态码有中文说明", () => {
    expect(errorMessage(404, "{}")).toBe("要操作的内容不存在");
    expect(errorMessage(429, "{}")).toBe("请求太频繁了，请稍候再试");
    expect(errorMessage(422, "{}")).toBe("输入的内容不合法");
  });

  it("未知状态码也给出可读信息，而不是空白", () => {
    expect(errorMessage(418, "{}")).toBe("请求失败（418）");
  });
});
