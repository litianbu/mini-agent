/**
 * React Query 的全局配置 + 所有"普通请求"的 hook。
 *
 * ── 为什么流式那段不放在这里 ──
 * React Query 擅长的是「请求 → 缓存 → 失效 → 重取」这一套。
 * 但聊天是**一次请求、持续推送、逐字出现**：
 *   · 没有"缓存"的概念（每次对话都是新的）
 *   · 没有"重取"的意义（重发一遍就是另一段对话）
 *   · 数据是无限追加的，不是一次到位的快照
 * 所以流式留在 useChatStream 里手写，这里只管能缓存的接口。
 *
 * 反过来，S5 的「会话列表 / 会话详情」就非常适合 React Query ——
 * 那时这里的代码会真正派上用场。
 */
import { useQuery } from "@tanstack/react-query";

export interface Health {
  ok: boolean;
  version: string;
  mock: boolean;
  model: string;
}

async function fetchHealth(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** 后端健康状况。5 秒内复用缓存，不重复请求。 */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    staleTime: 5_000,
    // 服务没起来时别一直重试，不然控制台会刷满报错
    retry: 1,
  });
}