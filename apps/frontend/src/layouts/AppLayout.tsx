/**
 * 整体布局：左边固定菜单，右边是路由内容。
 *
 * 关键点是 <Outlet />：它是个"占位坑"，子路由渲染出来的东西塞在这里。
 * 所以侧边栏只会渲染一次，切换页面时它不会重建 ——
 * 这就是为什么要用**布局路由**（layout route），而不是每个页面各写一遍侧边栏。
 *
 * 用 antd 的 Layout/Menu 后，这里几乎没有手写样式了：
 * 高度、分隔线、选中态都由组件负责。
 */
import { useState } from "react";
import { Badge, Layout, Menu, Typography } from "antd";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useHealth, useSessions } from "../api/client";
import { ActiveSessionContext } from "../context/activeSession";

const NEW_CHAT_KEY = "/";
const ALL_SESSIONS_KEY = "/sessions";
/** 子菜单本身的 key。它不对应任何路由，只是"一组"的标识。 */
export const SESSIONS_GROUP_KEY = "sessions-group";

/** /sessions/20 → 20；不是这个形状就返回 null。 */
export function sessionIdFromPath(pathname: string): number | null {
  const m = /^\/sessions\/(\d+)\/?$/.exec(pathname);
  return m ? Number(m[1]) : null;
}

/**
 * 当前路径该高亮哪一项菜单。
 *
 * 抽成纯函数是为了能单独测 —— 这个小逻辑已经错过一次
 * （/sessions/20 被判定成"新会话"）。
 *
 * 三种情况：
 *   /sessions/20 → 高亮那一条会话（子菜单项）
 *   /sessions    → 高亮"全部会话"
 *   其它          → 新会话
 */
export function activeNavKey(pathname: string): string {
  const id = sessionIdFromPath(pathname);
  if (id !== null) return `/sessions/${id}`;
  if (pathname === ALL_SESSIONS_KEY || pathname.startsWith(`${ALL_SESSIONS_KEY}/`)) {
    return ALL_SESSIONS_KEY;
  }
  return NEW_CHAT_KEY;
}

/** 侧栏里的小字。抽出来只是为了避免重复写 style。 */
function Muted({ children }: { children: React.ReactNode }) {
  return (
    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
      {children}
    </Typography.Text>
  );
}

export default function AppLayout() {
  const { pathname } = useLocation();

  // 聊天页会上报"我显示的是几号会话"。
  // 新会话拿到 id 后地址栏是 /sessions/20，但 react-router 并不知道
  // （用的是 replaceState），所以单看路径会一直停在"新会话"。
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);

  // 细节见 activeNavKey 的注释：这里必须按前缀归属，不能用相等判断
  const fromPath = activeNavKey(pathname);
  const wanted =
    fromPath === NEW_CHAT_KEY && activeSessionId != null
      ? `/sessions/${activeSessionId}`
      : fromPath;

  // 会话列表：侧栏直接列出来，点一条就进那段对话。
  const sessions = useSessions();
  const list = sessions.data ?? [];

  // /sessions/20 首次加载时列表还没回来，这个 key 在菜单里不存在，
  // antd 就什么都不高亮（看起来像"没选中"）。退回到"全部会话"更稳。
  const selected =
    wanted !== NEW_CHAT_KEY &&
    wanted !== ALL_SESSIONS_KEY &&
    !list.some((s) => `/sessions/${s.id}` === wanted)
      ? ALL_SESSIONS_KEY
      : wanted;

  // 这是 React Query 在本项目里的第一个真实用途：
  // 组件挂载时自动请求、卸载后缓存还在、多个组件用同一个 key 只发一次请求。
  const health = useHealth();

  return (
    <ActiveSessionContext.Provider value={{ activeSessionId, setActiveSessionId }}>
      <Layout style={{ height: "100vh" }}>
      <Layout.Sider
        width={220}
        theme="dark"
        /* styles.css 里的三段式布局挂在这个 class 上 */
        className="sider"
        style={{ borderInlineEnd: "1px solid rgba(255,255,255,.08)" }}
      >
        <div className="brand">
          <span className="brand-dot" />
          <Typography.Text strong>mini-agent</Typography.Text>
        </div>

        {/* 滚动容器。会话多了要能滚 —— 不能滚的话，超出可视区的会话
            就永远点不到了。样式见 styles.css 的 .nav-scroll */}
        <div className="nav-scroll">
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selected]}
            // 默认展开历史会话：刷新在 /sessions/20 时，用户一眼能看到自己那条
            defaultOpenKeys={[SESSIONS_GROUP_KEY]}
            items={[
              // 这两项是**导航**，不该跟着会话列表一起滚。
              // 会话可以无限多，导航永远只有两个 —— 混在一起滚，
              // 用户想点"新会话"还得先滚回顶部，很难受。
              {
                key: NEW_CHAT_KEY,
                label: <NavLink to={NEW_CHAT_KEY}>新会话</NavLink>,
              },
              {
                key: ALL_SESSIONS_KEY,
                label: <NavLink to={ALL_SESSIONS_KEY}>全部会话</NavLink>,
              },
              // 只有这一组会滚动（会话数量无上限）
              {
                key: SESSIONS_GROUP_KEY,
                label: "历史会话",
                children: [
                  // 会话本身作为子项。标题太长就省略（样式在 .nav-session）
                  ...list.map((s) => ({
                    key: `/sessions/${s.id}`,
                    label: (
                      <NavLink to={`/sessions/${s.id}`} className="nav-session">
                        {s.title || "未命名"}
                      </NavLink>
                    ),
                  })),
                  ...(list.length === 0 && !sessions.isPending
                    ? [
                        {
                          key: "no-sessions",
                          label: (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              还没有会话
                            </Typography.Text>
                          ),
                          disabled: true,
                        },
                      ]
                    : []),
                ],
              },
            ]}
            style={{ background: "transparent" }}
          />
        </div>

        <div className="sider-foot">
          {/* 三种状态都画出来：loading / 连不上 / 正常。
              平时没人注意它，但排查问题时会先看这一眼。 */}
          {health.isPending && (
            <Badge status="default" text={<Muted>连接中…</Muted>} />
          )}
          {health.isError && (
            <Badge status="error" text={<Muted>后端未连接</Muted>} />
          )}
          {health.data && (
            <Badge
              status={health.data.mock ? "warning" : "success"}
              text={
                <Muted>{health.data.mock ? "MOCK 模式" : health.data.model}</Muted>
              }
            />
          )}
        </div>
      </Layout.Sider>

        <Layout.Content style={{ overflow: "hidden", display: "flex" }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </ActiveSessionContext.Provider>
  );
}