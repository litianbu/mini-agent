/**
 * "当前正在聊哪个会话"——只给侧栏高亮用。
 *
 * 为什么需要它：
 *   新会话第一次发消息后，后端才分配 id。前端用 history.replaceState
 *   把地址栏改成 /sessions/20，但**没有通知 react-router**（故意的，
 *   见 NewSessionPage 里的注释：走路由会重挂载、把正在流的回答弄断）。
 *   于是 useLocation() 还停在 "/"，侧栏就以为你在"新会话"。
 *
 * 所以让聊天页主动上报：我显示的是几号会话。
 * 侧栏优先用这个值判断高亮，没有它再退回按路径判断。
 */
import { createContext, useContext } from "react";

type ActiveSession = {
  /** 当前聊天页显示的会话 id；新会话还没有 id 时是 null */
  activeSessionId: number | null;
  setActiveSessionId: (id: number | null) => void;
};

export const ActiveSessionContext = createContext<ActiveSession>({
  activeSessionId: null,
  // 占位实现：没有 Provider 时调用它不会炸
  setActiveSessionId: () => {},
});

export function useActiveSession() {
  return useContext(ActiveSessionContext);
}
