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

export interface DoneEvent {
  type: "done";
  finish_reason: string | null;
  ms: number;
  chars: number;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type AgentEvent =
  | StartEvent
  | TextEvent
  | ReasoningEvent
  | DoneEvent
  | ErrorEvent;