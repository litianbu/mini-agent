/**
 * 新会话页面。
 *
 * 注意这个文件现在有多薄 —— 所有"怎么收流、怎么取消、什么时候更新状态"
 * 都搬进了 useChatStream。页面只负责两件事：
 *   1. 把状态画出来
 *   2. 把用户输入交给 hook
 *
 * 这就是好分层的样子：想改界面来这里，想改行为去 hook。
 */
import { useState } from "react";
import { Alert, Button, Card, Flex, Input, Space, Tag, Typography } from "antd";
import { useChatStream } from "../hooks/useChatStream";

const STATUS_TAG = {
  idle: { color: "default", text: "空闲" },
  streaming: { color: "processing", text: "生成中…" },
  done: { color: "success", text: "完成" },
  error: { color: "error", text: "出错" },
} as const;

export default function NewSessionPage() {
  const [input, setInput] = useState("");
  const chat = useChatStream();

  const submit = () => {
    chat.send(input);
    setInput("");
  };

  return (
    <Flex vertical style={{ height: "100%", width: "100%" }}>
      {/* 顶栏：标题 + 模型 + 状态 */}
      <Flex
        align="center"
        gap={12}
        style={{
          padding: "12px 24px",
          borderBottom: "1px solid rgba(255,255,255,.08)",
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          新会话
        </Typography.Title>
        <Tag>{chat.model}</Tag>
        <Tag
          color={STATUS_TAG[chat.status].color}
          style={{ marginInlineStart: "auto" }}
        >
          {STATUS_TAG[chat.status].text}
        </Tag>
      </Flex>

      {/* 正文区：自己滚动 */}
      <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
        <div style={{ maxWidth: 820, margin: "0 auto" }}>
          {chat.reasoning && (
            <Card
              size="small"
              title={<Typography.Text type="secondary">思考过程</Typography.Text>}
              style={{ marginBottom: 16 }}
            >
              <Typography.Paragraph
                type="secondary"
                italic
                style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
              >
                {chat.reasoning}
              </Typography.Paragraph>
            </Card>
          )}

          {chat.text && (
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap", fontSize: 15 }}>
              {chat.text}
            </Typography.Paragraph>
          )}

          {!chat.text && !chat.reasoning && (
            <Typography.Text type="secondary">
              在下面问一句试试 —— 文字会一个字一个字出现。
            </Typography.Text>
          )}

          {chat.error && (
            <Alert
              type="error"
              showIcon
              message={chat.error}
              style={{ marginTop: 16 }}
            />
          )}

          {chat.meta && (
            <Typography.Text
              type="secondary"
              style={{ display: "block", marginTop: 16, fontSize: 12 }}
            >
              {chat.meta}
            </Typography.Text>
          )}
        </div>
      </div>

      {/* 输入区 */}
      <div style={{ padding: "12px 24px 20px" }}>
        <div style={{ maxWidth: 820, margin: "0 auto" }}>
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
            <Button
              type="primary"
              onClick={submit}
              disabled={chat.busy || !input.trim()}
            >
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