/**
 * 契约的 TypeScript 镜像 —— 对应后端 apps/backend/mini_agent/events.py。
 *
 * 为什么手写而不是自动生成？
 *   S1 阶段就 5 个类型，手写最省事且一眼能对。
 *   真项目里的做法是：FastAPI 自动暴露 /openapi.json，
 *   前端用 openapi-typescript 生成 client（S6 会讲到）。
 *   但即使自动生成，**先想清楚契约**这一步也不能省。
 *
 * 改后端 events.py 时，记得同步改这个文件。
 */

export interface StartEvent {
  type: "start";
  model: string;
  mock: boolean;
}

export interface TextEvent {
  type: "text";
  delta: string;
}

export interface ReasoningEvent {
  type: "reasoning";
  delta: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  name: string;
  arguments: string;
}

export interface ToolResultEvent {
  type: "tool_result";
  id: string;
  name: string;
  ok: boolean;
  output: string;
}

export interface DoneEvent {
  type: "done";
  finish_reason: string | null;
  ms: number;
  chars: number;
  rounds: number;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type AgentEvent =
  | StartEvent
  | TextEvent
  | ReasoningEvent
  | ToolCallEvent
  | ToolResultEvent
  | DoneEvent
  | ErrorEvent;

/** 一次工具调用的完整记录：调用 + 结果拼在一起，方便渲染 */
export interface ToolStep {
  id: string;
  name: string;
  arguments: string;
  done: boolean;
  ok: boolean;
  output: string;
}
/** 界面上的一条消息（用户问的，或助手答的）。 */
export interface Bubble {
  id: string;
  role: "user" | "assistant";
  /** 正文。用户消息就是原问题；助手消息是流式拼起来的回答 */
  text: string;
  /** 助手的思考过程（用户消息永远为空） */
  reasoning: string;
  /** 助手在这条消息里调用过的工具 */
  tools: ToolStep[];
}
