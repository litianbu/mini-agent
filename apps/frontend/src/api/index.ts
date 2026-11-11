/**
 * 前端唯一的网络层：把 SSE 流解析回事件。
 *
 * 为什么不用 EventSource？
 *   1. EventSource 只能发 GET，而我们要把问题放进 POST body；
 *   2. EventSource 不能自定义请求头，也没法 abort。
 * 所以用 fetch + ReadableStream 自己解析 —— 多写 20 行，换来完全的控制权。
 *
 * 这段解析和后端 apps/backend/mini_agent/agent/events.py 的 to_sse() 是一对：
 * 那边用 `data: {...}\n\n` 写，这边就用同样的规则读。
 */
import type { AgentEvent } from "../types";

export async function* chatStream(
  question: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail.slice(0, 200)}`);
  }
  if (!res.body) throw new Error("响应没有 body，无法读取流");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // stream: true —— 少写这个，中文会被切坏（和后端解析上游是同一个坑）
    buf += decoder.decode(value, { stream: true });

    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);

      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          yield JSON.parse(line.slice(5).trim()) as AgentEvent;
        } catch {
          /* 心跳等非 JSON 帧，忽略 */
        }
      }
    }
  }
}
/** 流式过程中页面的全部状态（hook 与页面共享这一个形状）。 */
export interface ChatStreamState {
  text: string;
  reasoning: string;
  status: "idle" | "streaming" | "done" | "error";
  model: string;
  meta: string;
  error: string;
}
