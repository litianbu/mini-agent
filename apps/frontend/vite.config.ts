import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

  server: {
    // 不用 Vite 默认的 5173：这台机器上还有别的项目占着它。
    // 端口被占时 Vite 会自动 +1，但请以终端打印的地址为准。
    port: 5180,

    // 前端 5180、后端 8787，跨端口。
    // 用代理把 /api 转给后端，浏览器眼里就是「同源」——
    // 这样完全不用配 CORS，也不会踩预检请求的坑。
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: "dist",
    emptyOutDir: true,

    // 拆包：react 和 antd 这种"半年不变"的依赖单独打包。
    // 好处是改了业务代码，用户浏览器里缓存的 vendor 包依然有效 ——
    // 否则每改一行都让用户重下 800KB。
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          antd: ["antd"],
          query: ["@tanstack/react-query"],
          // markdown 相关单独一块：平时很少变，
          // 分开后改了业务代码也不会让这 100 多 KB 的缓存失效
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },

    // vendor 包本来就大，把警告阈值提上去，免得每次构建都刷一条假警报
    chunkSizeWarningLimit: 900,
  },
});