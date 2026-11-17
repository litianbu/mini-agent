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
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { chatStream } from "../api";
import type { Bubble, ToolStep } from "../types";
import { metaLine } from "../lib/finishNote";

export type ChatStatus = "idle" | "streaming" | "done" | "error";

export interface ChatStream {
  bubbles: Bubble[];
  /** 当前会话 id：新会话在收到 start 事件后才有值 */
  sessionId: number | undefined;
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

/**
 * @param initialBubbles 历史会话回放时传进来；新会话传空数组
 * @param sessionId      已有会话的 id；新会话传 undefined（由后端分配）
 */
export function useChatStream(
  initialBubbles: Bubble[] = [],
  sessionId?: number,
): ChatStream {
  const [bubbles, setBubbles] = useState<Bubble[]>(initialBubbles);
  // 新会话时后端会在 start 事件里给 id，拿到后存这里
  const [sid, setSid] = useState<number | undefined>(sessionId);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [model, setModel] = useState("—");
  const [meta, setMeta] = useState("");
  const [error, setError] = useState("");

  // 侧栏的会话列表是 React Query 管的。这一轮对话结束后，
  // 侧栏必须跟着变（新会话要出现在列表里、条数要 +1、顺序要往前排），
  // 所以在这里主动让它失效重取 —— 这是"写操作影响读缓存"的标准做法。
  const qc = useQueryClient();

  const aborter = useRef<AbortController | null>(null);
  // ref 而不是 state：send 是 useCallback 缓存的，闭包里读到的是旧 state
  const sidRef = useRef<number | undefined>(sessionId);

  const stop = useCallback(() => {
    aborter.current?.abort();
  }, []);

  // 组件卸载时中止进行中的流。
  //
  // 不加这段会发生什么：用户在生成中途跳到别的页面（比如侧栏的会话列表），
  // 组件没了，但 fetch 还连着 —— 浏览器不会自动关连接，
  // 后端就继续跑完整个流程（最多 8 轮工具调用），为已经离开的人烧钱。
  //
  // 后端的取消传播只在"连接真的断开"时才有机会生效，
  // 而连接断不断，前端说了算。所以这两个必须成对出现。
  useEffect(() => {
    return () => {
      aborter.current?.abort();
    };
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
        // 注意用 sidRef：send 的闭包拿不到最新的 sid
        for await (const ev of chatStream(q, sidRef.current, ctrl.signal)) {
          switch (ev.type) {
            case "start":
              setModel(ev.mock ? "MOCK（本地假数据）" : ev.model);
              if (ev.session_id != null) {
                setSid(ev.session_id);
                sidRef.current = ev.session_id;
              }
              // 会话在流开始前就已经建好了，此时刷新列表能让侧栏立刻出现它
              void qc.invalidateQueries({ queryKey: ["sessions"] });
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
              // 回答存库后消息数变了，列表要重取才能显示新的条数/顺序
              void qc.invalidateQueries({ queryKey: ["sessions"] });
              // 正常结束时不显示任何东西（metaLine 返回空串）；
              // 开发环境才会带上轮数/耗时/token 这些排查用的信息。
              setMeta(metaLine(ev, import.meta.env.DEV));
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
          // 不再一律加"网络错误"前缀 —— 后端返回的 429/422
          // 已经被翻译成人话了，前缀会把"服务端在拒绝"说成"网络有问题"，
          // 排查方向直接跑偏。
          setError(describe(e));
        }
      } finally {
        aborter.current = null;
      }
    })();
  }, []);

  return {
    bubbles,
    sessionId: sid,
    status,
    model,
    meta,
    error,
    busy: status === "streaming",
    send,
    stop,
  };
}

/**
 * 把异常变成一句人话。
 *
 * 真正的网络故障（后端没起、断网）时，fetch 抛的是 TypeError，
 * message 是英文的 "Failed to fetch" —— 对用户毫无信息量。
 * 后端返回的 HTTP 错误则已经在 api/errors.ts 里翻译过了，直接用。
 */
function describe(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return "连不上后端，检查服务是否在运行";
  }
  return msg || "请求失败";
}
