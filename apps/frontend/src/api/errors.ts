/**
 * 把后端的错误响应翻译成一句人能看懂的话。
 *
 * 为什么要单独做这件事：
 *   原来是把响应体整段拼进错误消息里，用户看到的是
 *
 *     网络错误：HTTP 422 {"detail":[{"type":"string_too_long",
 *     "loc":["body","question"],"msg":"String should have at most 8000 characters",
 *     "input":"# 任务：审查一段..."}]}
 *
 *   ——英文、内部字段名、还把用户的输入原样回显了一遍。
 *   更糟的是它说"网络错误"，但网络其实是好的，会把人往错的方向引。
 */
import type { ErrorDetail } from "../types";

/**
 * @param status HTTP 状态码
 * @param body   响应体原文（可能不是 JSON，不要假设）
 */
export function errorMessage(status: number, body: string): string {
  let detail: unknown;
  try {
    detail = JSON.parse(body)?.detail;
  } catch {
    // 不是 JSON：可能网关返回了 HTML 错误页
    return fallback(status);
  }

  // FastAPI 自己抛的 HTTPException：detail 是一句人话
  if (typeof detail === "string") return detail;

  // 入参校验失败：detail 是数组，每条带 loc 和 msg
  if (Array.isArray(detail)) {
    const first = detail[0] as ErrorDetail | undefined;
    if (first) {
      const field = first.loc?.[first.loc.length - 1];
      return `输入不合法${field ? `（${field}）` : ""}：${first.msg}`;
    }
  }

  return fallback(status);
}

function fallback(status: number): string {
  switch (status) {
    case 404:
      return "要操作的内容不存在";
    case 422:
      return "输入的内容不合法";
    case 429:
      return "请求太频繁了，请稍候再试";
    case 500:
      return "服务端出错了";
    default:
      return `请求失败（${status}）`;
  }
}
