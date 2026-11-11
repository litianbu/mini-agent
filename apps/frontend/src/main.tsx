import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "antd/dist/reset.css";
import "./styles.css";

// React Query 的缓存中枢。整个应用共用一个实例。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 用户切窗口回来时自动重取 —— 后台类界面基本都想要这个行为
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* BrowserRouter 管地址栏（History API）；
        换 HashRouter 就会变成 /#/sessions 那种带井号的地址。 */}
    <BrowserRouter>
      {/* QueryClientProvider 必须在最外层之一：
          任何组件里的 useQuery 都要能找到这个 client。 */}
      <QueryClientProvider client={queryClient}>
        {/* ConfigProvider 是 antd 的全局开关：
            zhCN 让分页/空状态这些内置文案变中文，
            darkAlgorithm 用暗色主题（我们整个界面都是暗的）。
            AntApp 提供 message/notification 的上下文。 */}
        <ConfigProvider
          locale={zhCN}
          theme={{ algorithm: theme.darkAlgorithm, token: { borderRadius: 8 } }}
        >
          <AntApp>
            <App />
          </AntApp>
        </ConfigProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);