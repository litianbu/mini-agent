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
import { Badge, Layout, Menu, Typography } from "antd";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useHealth } from "../api/client";

const NAV = [
  { key: "/", label: "新会话" },
  { key: "/sessions", label: "历史会话" },
];

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

  // Menu 用 key 判断选中项；手动同步给当前路径，
  // 这样直接输网址进来高亮也是对的。
  const selected = NAV.some((n) => n.key === pathname) ? pathname : "/";

  // 这是 React Query 在本项目里的第一个真实用途：
  // 组件挂载时自动请求、卸载后缓存还在、多个组件用同一个 key 只发一次请求。
  const health = useHealth();

  return (
    <Layout style={{ height: "100vh" }}>
      <Layout.Sider
        width={220}
        theme="dark"
        style={{ borderInlineEnd: "1px solid rgba(255,255,255,.08)" }}
      >
        <div className="brand">
          <span className="brand-dot" />
          <Typography.Text strong>mini-agent</Typography.Text>
        </div>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selected]}
          items={NAV.map((n) => ({
            // 用 NavLink 作为标签，才有真正的 <a> 和客户端跳转
            key: n.key,
            label: <NavLink to={n.key}>{n.label}</NavLink>,
          }))}
          style={{ background: "transparent" }}
        />

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
  );
}