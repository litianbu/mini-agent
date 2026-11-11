/**
 * 路由表。就这么点东西 —— 但它是"应用"和"一个页面"的分界线。
 *
 * 布局路由（父级不带 path 的那条）负责外壳：侧边栏只渲染一次。
 * 子路由只换右边的内容区。
 */
import { Result } from "antd";
import { Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import NewSessionPage from "./pages/NewSessionPage";
import SessionsPage from "./pages/SessionsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        {/* index = 路径为 "/" 时渲染，也就是默认打开新会话 */}
        <Route index element={<NewSessionPage />} />
        <Route path="sessions" element={<SessionsPage />} />

        {/* 兜底：地址打错时不至于白屏 */}
        <Route
          path="*"
          element={
            <Result
              status="404"
              title="页面不存在"
              subTitle="地址打错了 —— 从左边菜单选一个。"
            />
          }
        />
      </Route>
    </Routes>
  );
}