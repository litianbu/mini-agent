/**
 * 一轮对话结束后，那行灰色小字写什么。
 *
 * 原则：**成功时不打扰，出问题才说话**。
 *
 * 一开始我把 `finish_reason`、字符数、耗时、轮数、token 全拼上去，
 * 结果是界面上挂着一串只有开发者看得懂的东西：
 *
 *     tool_calls · 0 字 · 32718ms · 8 轮 · 53644 token（入 51197 / 出 2447）
 *
 * 用户不关心这个。他关心的是"刚才那下成功了吗？出什么问题了？"
 *
 * 所以这里分成两路：
 *   - finishNote：给用户看的，正常结束时返回 null（什么都不显示）
 *   - debugNote：给开发看的，只有 DEV 环境才出现
 */
import type { DoneEvent } from "../types";

/**
 * 用户该看到的一句话。正常结束、或者已经有错误提示时返回 null。
 *
 * `tool_calls` 特意返回 null：这个情况后端会先发 ErrorEvent
 * （"已连续调用工具 8 轮仍未结束，强制停止"），界面已经有红色提示了。
 * 再补一行灰字说明同一件事，属于噪音。
 */
export function finishNote(ev: DoneEvent): string | null {
  switch (ev.finish_reason) {
    case "stop":
    case null:
      return null; // 正常说完，不用解释

    case "length":
      return "回答太长，被截断了";

    case "tool_calls":
      // 后端已经发过 ErrorEvent 说明这件事了，不重复
      return null;

    case "content_filter":
      return "内容被安全策略拦截";

    default:
      // 没见过的情况：与其让用户猜，不如把原始值给他
      return `这次没有正常结束（${ev.finish_reason}）`;
  }
}

/** 开发时才看得懂的完整信息。 */
export function debugNote(ev: DoneEvent): string {
  const parts = [
    `finish=${ev.finish_reason ?? "?"}`,
    `${ev.rounds} 轮`,
    `${ev.chars} 字`,
    `${(ev.ms / 1000).toFixed(1)}s`,
  ];
  // token 为 0 说明网关没返回（比如 mock 模式），不显示免得误导
  if (ev.total_tokens > 0) {
    parts.push(`${ev.total_tokens} token（入 ${ev.prompt_tokens} / 出 ${ev.completion_tokens}）`);
  }
  // 裁剪悄悄发生会变成"明明说过的事它怎么忘了"这种玄学问题，
  // 所以在开发环境一定要能看到
  if (ev.trimmed > 0) {
    parts.push(`裁掉 ${ev.trimmed} 条历史`);
  }
  return parts.join(" · ");
}

/**
 * 最终显示的那行字。
 *
 * @param dev 传 import.meta.env.DEV。开发环境把调试信息也带上。
 */
export function metaLine(ev: DoneEvent, dev: boolean): string {
  const note = finishNote(ev);
  const debug = dev ? debugNote(ev) : "";

  if (!dev) return note ?? "";
  // 开发环境：调试信息打头，出问题时把用户提示也带上
  return note ? `${note} · ${debug}` : debug;
}
