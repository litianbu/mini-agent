/**
 * 聊天流的自定义 hook。
 *
 * 把「发请求 → 收事件 → 更新状态」从页面里抽出来，页面只负责画。
 *
 * 为什么不用 React Query？见 api/client.ts 顶部那段话。
 *
 * 状态形状：一个 bubbles 数组，用户消息和助手消息混在一起按顺序排。
 * 为什么不是「一个 text 字符串」——因为一次会话会有多轮问答，
 * 只存一个字符串的话，上一轮的回答会被下一轮冲掉，用户看不到自己问过什么。
 */
import { useCallback, useRef, useState } from "react";
import { chatStream } from "../api";
import type { Bubble, ToolStep } from "../types";

export type ChatStatus = "idle" | "streaming" | "done" | "error";

export interface ChatStream {
  bubbles: Bubble[];
  status: ChatStatus;
  model: string;
  meta: string;
  error: string;
  busy: boolean;
  send: (question: string) => void;
  stop: () => void;
}

let seq = 0;
const nextId = () => `m${++seq}`;

export function useChatStream(): ChatStream {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [model, setModel] = useState("—");
  const [meta, setMeta] = useState("");
  const [error, setError] = useState("");

  const aborter = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    aborter.current?.abort();
  }, []);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q) return;

    // 上一次还没结束就先中止，避免两个流同时往里写
    aborter.current?.abort();

    setMeta("");
    setError("");
    setStatus("streaming");

    // 用户消息立刻上屏 —— 这才是"用户内容要展示"的关键。
    // 助手的那条先占位（内容为空，页面会跳过不画），
    // 这样后续 delta 有个固定的 target 可写。
    const userBubble: Bubble = {
      id: nextId(),
      role: "user",
      text: q,
      reasoning: "",
      tools: [],
    };
    const botBubble: Bubble = {
      id: nextId(),
      role: "assistant",
      text: "",
      reasoning: "",
      tools: [],
    };
    setBubbles((list) => [...list, userBubble, botBubble]);

    // 只更新助手那一条的某个字段。用 id 定位，
    // 因为闭包里的 list 可能已经过时了。
    const patchBot = (fn: (b: Bubble) => Bubble) =>
      setBubbles((list) => list.map((b) => (b.id === botBubble.id ? fn(b) : b)));

    const ctrl = new AbortController();
    aborter.current = ctrl;

    void (async () => {
      try {
        for await (const ev of chatStream(q, ctrl.signal)) {
          switch (ev.type) {
            case "start":
              setModel(ev.mock ? "MOCK（本地假数据）" : ev.model);
              break;
            case "text":
              patchBot((b) => ({ ...b, text: b.text + ev.delta }));
              break;
            case "reasoning":
              patchBot((b) => ({ ...b, reasoning: b.reasoning + ev.delta }));
              break;
            case "tool_call":
              // 先是"它要做什么"。结果还没回来，先记成 done=false
              patchBot((b) => ({
                ...b,
                tools: [
                  ...b.tools,
                  {
                    id: ev.id,
                    name: ev.name,
                    arguments: ev.arguments,
                    done: false,
                    ok: false,
                    output: "",
                  },
                ],
              }));
              break;
            case "tool_result":
              // 结果回来，按 id 找到那条记录补上
              patchBot((b) => ({
                ...b,
                tools: b.tools.map((t): ToolStep =>
                  t.id === ev.id
                    ? { ...t, done: true, ok: ev.ok, output: ev.output }
                    : t,
                ),
              }));
              break;
            case "done":
              setStatus("done");
              setMeta(
                `${ev.finish_reason ?? "?"} · ${ev.chars} 字 · ${ev.ms}ms · ${ev.rounds} 轮`,
              );
              break;
            case "error":
              setStatus("error");
              setError(ev.message);
              break;
          }
        }
      } catch (e) {
        // 用户点「停止」时 fetch 会抛 AbortError —— 这不是错误
        if ((e as Error).name === "AbortError") {
          setStatus("done");
          setMeta("已停止");
        } else {
          setStatus("error");
          setError(`网络错误：${(e as Error).message}`);
        }
      } finally {
        aborter.current = null;
      }
    })();
  }, []);

  return {
    bubbles,
    status,
    model,
    meta,
    error,
    busy: status === "streaming",
    send,
    stop,
  };
}
