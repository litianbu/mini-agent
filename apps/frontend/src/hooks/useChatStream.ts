/**
 * 聊天流的自定义 hook。
 *
 * 把「发请求 → 收事件 → 更新状态」从页面里抽出来，好处有三：
 *   1. 页面只关心渲染，逻辑可单独测
 *   2. 停止/清理的时机集中在一处（不会忘记 abort）
 *   3. S3 加工具事件时，只改这个文件，页面几乎不动
 *
 * 为什么不用 React Query？见 api/client.ts 顶部那段话。
 */
import { useCallback, useRef, useState } from "react";
import { chatStream, type ChatStreamState } from "../api";

export interface ChatStream extends ChatStreamState {
  busy: boolean;
  send: (question: string) => void;
  stop: () => void;
}

export function useChatStream(): ChatStream {
  const [text, setText] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [status, setStatus] = useState<ChatStreamState["status"]>("idle");
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

    setText("");
    setReasoning("");
    setMeta("");
    setError("");
    setStatus("streaming");

    const ctrl = new AbortController();
    aborter.current = ctrl;

    // 这里不 await：调用方拿不到 promise，也不用管生命周期
    void (async () => {
      try {
        for await (const ev of chatStream(q, ctrl.signal)) {
          switch (ev.type) {
            case "start":
              setModel(ev.mock ? "MOCK（本地假数据）" : ev.model);
              break;
            case "text":
              setText((t) => t + ev.delta);
              break;
            case "reasoning":
              setReasoning((r) => r + ev.delta);
              break;
            case "done":
              setStatus("done");
              setMeta(
                `${ev.finish_reason ?? "?"} · ${ev.chars} 字 · ${ev.ms}ms`,
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
    text,
    reasoning,
    status,
    model,
    meta,
    error,
    busy: status === "streaming",
    send,
    stop,
  };
}