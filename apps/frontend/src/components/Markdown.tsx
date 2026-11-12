/**
 * Markdown 渲染。
 *
 * 为什么需要：模型回答里几乎必然带 ```代码块```、列表、表格。
 * 全当纯文本显示的话，`**加粗**` 会原样露出星号，代码块也没有缩进，
 * 读起来很难受。
 *
 * 为什么用 react-markdown 而不是手写解析：
 *   Markdown 规范细节极多（嵌套列表、表格对齐、转义……），
 *   自己写一定会在奇怪的地方翻车。这是"该用库就用库"的典型场景。
 *
 * 安全：react-markdown 默认**不渲染原始 HTML**（不装 rehype-raw），
 * 所以模型输出里写 <script> 也只会当文字显示。这点很重要 ——
 * 模型输出是不可信内容，和用户输入同级。
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 链接一律新窗口打开：避免把当前会话页面顶掉
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {c}
            </a>
          ),
          // 代码块横向滚动，不要把气泡撑破
          pre: ({ children: c }) => <pre className="md-pre">{c}</pre>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
