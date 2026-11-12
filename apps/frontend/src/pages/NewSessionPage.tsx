/**
 * 新会话页面。
 *
 * 这个文件只做两件事：
 *   1. 把 bubbles 数组画出来（用户消息在右，助手在左）
 *   2. 把用户输入交给 hook
 *
 * 想改界面来这里，想改行为去 hooks/useChatStream.ts。
 */
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Flex,
  Input,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { Markdown } from "../components/Markdown";
import { useChatStream } from "../hooks/useChatStream";
import type { Bubble } from "../types";

const STATUS_TAG = {
  idle: { color: "default", text: "空闲" },
  streaming: { color: "processing", text: "生成中…" },
  done: { color: "success", text: "完成" },
  error: { color: "error", text: "出错" },
} as const;

/** 收起状态下也要能看出结果，所以把状态压成几个字放在标题后面。 */
function toolSummary(tools: Bubble["tools"]): string {
  if (tools.some((t) => !t.done)) return "· 执行中…";
  const failed = tools.filter((t) => !t.ok).length;
  return failed === 0 ? "· 全部成功" : `· ${failed} 个失败`;
}

/** 一条消息。用户的和助手的长得不一样，这是"谁在说话"最直观的提示。 */
function Message({ bubble }: { bubble: Bubble }) {
  const isUser = bubble.role === "user";

  // 助手这条还没吐任何东西时不要占位（刚发的请求还在等第一帧）
  const empty =
    !isUser && !bubble.text && !bubble.reasoning && bubble.tools.length === 0;
  if (empty) return null;

  return (
    <Flex
      vertical
      gap={8}
      align={isUser ? "flex-end" : "flex-start"}
      style={{ marginBottom: 20 }}
    >
      <div className={`msg ${isUser ? "msg-user" : "msg-bot"}`}>
        {/* 用户消息原样显示，保留换行 */}
        {isUser ? (
          <div className="msg-text">{bubble.text}</div>
        ) : (
          <>
            {bubble.reasoning && (
              <details className="msg-reasoning">
                <summary>思考过程</summary>
                <div>{bubble.reasoning}</div>
              </details>
            )}

            {bubble.tools.length > 0 && (
              // 用 <details> 而不是 antd Card：折叠/展开是浏览器原生行为，
              // 不需要 state，还自带无障碍支持（键盘能 Tab 到 summary）。
              // 默认收起 —— 工具输出往往很长，全展开会把回答挤到屏幕外。
              <details className="msg-tools">
                <summary>
                  工具调用 {bubble.tools.length} 个
                  <span className="msg-tools-state">{toolSummary(bubble.tools)}</span>
                </summary>

                <Timeline
                  className="msg-tools-body"
                  items={bubble.tools.map((t) => ({
                    color: t.done ? (t.ok ? "green" : "red") : "blue",
                    children: (
                      <Flex vertical gap={2}>
                        <Typography.Text code>{t.name}</Typography.Text>
                        <Typography.Text type="secondary" className="msg-arg">
                          {t.arguments}
                        </Typography.Text>
                        {t.done && (
                          <Typography.Text
                            className="msg-out"
                            style={{ color: t.ok ? undefined : "#ff7875" }}
                          >
                            {t.output.slice(0, 300)}
                            {t.output.length > 300 ? "…" : ""}
                          </Typography.Text>
                        )}
                      </Flex>
                    ),
                  }))}
                />
              </details>
            )}

            {bubble.text && <Markdown>{bubble.text}</Markdown>}
          </>
        )}
      </div>
    </Flex>
  );
}

export default function NewSessionPage() {
  const [input, setInput] = useState("");
  const chat = useChatStream();
  const bottom = useRef<HTMLDivElement>(null);

  // 新内容出现时自动滚到底部，否则流式输出时用户得手动拖
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [chat.bubbles]);

  const submit = () => {
    chat.send(input);
    setInput("");
  };

  return (
    <Flex vertical style={{ height: "100%", width: "100%" }}>
      {/* 顶栏 */}
      <Flex align="center" gap={12} className="chat-head">
        <Typography.Title level={5} style={{ margin: 0 }}>
          新会话
        </Typography.Title>
        <Tag>{chat.model}</Tag>
        <Tag color={STATUS_TAG[chat.status].color} style={{ marginInlineStart: "auto" }}>
          {STATUS_TAG[chat.status].text}
        </Tag>
      </Flex>

      {/* 消息区：自己滚动 */}
      <div className="chat-scroll">
        <div className="chat-body">
          {chat.bubbles.map((b) => (
            <Message key={b.id} bubble={b} />
          ))}

          {chat.bubbles.length === 0 && (
            <Typography.Text type="secondary">
              在下面问一句试试 —— 它可以用工具读文件、跑命令。
            </Typography.Text>
          )}

          {chat.error && (
            <Alert type="error" showIcon message={chat.error} style={{ marginTop: 8 }} />
          )}

          {chat.meta && (
            <Typography.Text type="secondary" className="chat-meta">
              {chat.meta}
            </Typography.Text>
          )}

          <div ref={bottom} />
        </div>
      </div>

      {/* 输入区 */}
      <div className="chat-input">
        <div className="chat-body">
          <Space.Compact style={{ width: "100%", alignItems: "flex-end" }}>
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={chat.busy}
            />
            <Button type="primary" onClick={submit} disabled={chat.busy || !input.trim()}>
              发送
            </Button>
            <Button danger onClick={chat.stop} disabled={!chat.busy}>
              停止
            </Button>
          </Space.Compact>
        </div>
      </div>
    </Flex>
  );
}
