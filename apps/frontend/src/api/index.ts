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
import { errorMessage } from "./errors";
import type { AgentEvent } from "../types";

export async function* chatStream(
  question: string,
  sessionId: number | undefined,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // sessionId 为空 = 新会话，后端会建一个并通过 start 事件发回 id
    body: JSON.stringify({ question, session_id: sessionId ?? null }),
    signal,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(errorMessage(res.status, body));
  }
  if (!res.body) throw new Error("响应没有 body，无法读取流");

  // res.body 是一个 ReadableStream：数据是一块块到的，不能用 res.json() 一次性拿完。
  // getReader() 拿到流上的"读锁"（一条流同时只能有一个 reader），
  // 之后靠循环 reader.read() 边到边解析 —— 这就是"流式"在前端的落点。
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  try {
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
  } finally {
    // 无论如何都要释放读锁。用户点停止时 reader.read() 会抛 AbortError，
    // 不 cancel 的话这条流会一直挂在那儿，占着连接。
    try {
      await reader.cancel();
    } catch {
      /* 已经断了就别再抱怨 */
    }
  }
}

/** 流式过程中页面的全部状态（hook 与页面共享这一个形状）。 */
