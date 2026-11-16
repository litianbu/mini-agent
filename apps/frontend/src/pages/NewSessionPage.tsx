/**
 * 聊天页面。两种进法用同一个组件：
 *   /              → 新会话（sessionId 为空，后端分配）
 *   /sessions/:id  → 打开历史会话（先读库，再接着聊）
 *
 * ── 这里踩过一个坑，值得记住 ──
 * 原来所有状态都在这一个组件里：先进 useSession() 拿数据，
 * 再把数据算成 initialBubbles 传给 useChatStream()。
 * 结果是"从历史会话进去偶尔是空白的"。
 *
 * 原因：useState(initialBubbles) 只在**第一次渲染**时看这个值。
 * 首次进入时请求还没回来，initialBubbles 是空数组；
 * 数据到了、组件重渲染，但 useState 的初始值早就用掉了，不会更新。
 * 之所以"偶尔"正常 —— React Query 有缓存，第二次进同一个会话时
 * 首次渲染就有数据了。
 *
 * 正确做法不是"用 useEffect 把新数据塞进 state"（那样要处理
 * 流式数据和历史数据的覆盖顺序），而是：**让这个组件晚点出生**。
 * 等数据到了再挂载它，初始值自然就是对的。下面的 key 干的就是这件事。
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Flex,
  Input,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { Markdown } from "../components/Markdown";
import { useSession } from "../api/client";
import { useActiveSession } from "../context/activeSession";
import { useChatStream } from "../hooks/useChatStream";
import type { Bubble, StoredMessage } from "../types";

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

/** 库里的消息 → 界面气泡。纯函数，方便单独测。 */
export function toBubbles(messages: StoredMessage[] | undefined): Bubble[] {
  return (messages ?? [])
    // 工具结果（role=tool）没有正文，界面上也没有对应气泡，跳过
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      id: `db-${m.id}`,
      role: m.role as "user" | "assistant",
      text: m.content,
      reasoning: m.reasoning ?? "",
      tools: [],
    }));
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

/**
 * 真正的聊天界面。
 *
 * 只在"该显示什么"已经确定之后才挂载 ——
 * 历史会话要等数据回来，新会话立刻就能挂载（初始气泡为空）。
 */
function ChatView({
  initialBubbles,
  sessionId,
  title,
}: {
  initialBubbles: Bubble[];
  sessionId?: number;
  title?: string;
}) {
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const chat = useChatStream(initialBubbles, sessionId);

  // 告诉侧栏"我现在显示的是几号会话"，它据此高亮。
  //
  // 用 useLayoutEffect 而不是 useEffect：这个值要在浏览器绘制之前写好，
  // 否则会有一帧的高亮错位（侧栏先按旧路径画一次，再跳到新值）。
  const { setActiveSessionId } = useActiveSession();
  const shownId = sessionId ?? chat.sessionId ?? null;
  useLayoutEffect(() => {
    setActiveSessionId(shownId);
  }, [shownId, setActiveSessionId]);

  // 新会话第一次发消息后，后端会分配 id。
  // 把它写进地址栏 —— 这样刷新还能回到这段对话。
  //
  // 为什么用 history.replaceState 而不是 react-router 的 navigate：
  //   navigate 会让路由真正切换，而 "/" 和 "/sessions/:id" 是两个路由，
  //   组件会被卸载重挂 → 正在进行的流断了，已经收到的文字也丢了。
  //   replaceState 只改地址栏，不通知路由，组件不受影响。
  useEffect(() => {
    if (sessionId == null && chat.sessionId != null) {
      window.history.replaceState(null, "", `/sessions/${chat.sessionId}`);
    }
  }, [chat.sessionId, sessionId]);

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
        <Typography.Title level={5} style={{ margin: 0 }} ellipsis>
          {title || "新会话"}
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

export default function NewSessionPage() {
  // 路由是 /sessions/:id 时 id 有值，/ 时为空
  const { id } = useParams();
  const sessionId = id ? Number(id) : undefined;

  const { data: detail, isLoading, error } = useSession(sessionId);

  // 历史会话：数据没到之前先别挂载聊天组件（原因见文件顶部那段）
  if (sessionId !== undefined && isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: "100%" }}>
        <Spin tip="正在读取会话…">
          <div style={{ width: 120, height: 40 }} />
        </Spin>
      </Flex>
    );
  }

  if (sessionId !== undefined && error) {
    return (
      <Flex align="center" justify="center" style={{ height: "100%", padding: 24 }}>
        <Alert
          type="error"
          showIcon
          message="这个会话读不出来"
          description={`${String(error)}。可能是已被删除。`}
        />
      </Flex>
    );
  }

  // key 是关键：换了会话（或从"新会话"第一次拿到 id）就换一个实例，
  // 让 useChatStream 的初始值重新生效。
  return (
    <ChatView
      key={sessionId ?? "new"}
      initialBubbles={toBubbles(detail?.messages)}
      sessionId={sessionId}
      title={detail?.title}
    />
  );
}
