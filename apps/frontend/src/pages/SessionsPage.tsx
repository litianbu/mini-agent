/**
 * 历史会话列表。
 *
 * 数据结构就是后端的 GET /api/sessions：会话 + 消息条数。
 * 点一行进聊天页，删除按钮调 DELETE。
 */
import { Alert, Button, Empty, Flex, Popconfirm, Spin, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useDeleteSession, useSessions } from "../api/client";

/** 相对时间：侧栏里"3 分钟前"比"2026-11-12 18:45:11"好读得多。 */
function ago(iso: string): string {
  // 后端返回的没有时区后缀，按本地时间解析即可
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  return `${Math.floor(hour / 24)} 天前`;
}

export default function SessionsPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useSessions();
  const del = useDeleteSession();

  return (
    <Flex vertical style={{ height: "100%", width: "100%" }}>
      <Flex align="center" gap={12} className="chat-head">
        <Typography.Title level={5} style={{ margin: 0 }}>
          历史会话
        </Typography.Title>
        <Button size="small" onClick={() => navigate("/")} style={{ marginInlineStart: "auto" }}>
          新会话
        </Button>
      </Flex>

      <div className="chat-scroll">
        <div className="chat-body">
          {error && <Alert type="error" showIcon message={`加载失败：${String(error)}`} />}

          {isLoading && <Spin />}

          {!isLoading && data?.length === 0 && (
            <Empty
              description={
                <Typography.Text type="secondary">
                  还没有会话 —— 去问一句，就会出现在这里。
                </Typography.Text>
              }
            />
          )}

          {data && data.length > 0 && (
            // 这里没用 antd 的 List：antd 6 已把它标为废弃，
            // 替代品 Listy 是虚拟滚动组件（要固定行高 + 容器高度），
            // 为一个几十条的列表引入这层复杂度不划算。原生 div 更简单。
            <div className="session-list">
              {data.map((s) => (
                <div
                  key={s.id}
                  className="session-row"
                  onClick={() => navigate(`/sessions/${s.id}`)}
                >
                  <div className="session-main">
                    <div className="session-title">{s.title}</div>
                    <div className="session-sub">{ago(s.updated_at)}</div>
                  </div>

                  <Tag className="session-count">{s.message_count} 条</Tag>

                  <Popconfirm
                    title="删除这个会话？"
                    description="消息会一起删掉，不能恢复。"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => del.mutate(s.id)}
                  >
                    <Button
                      size="small"
                      danger
                      type="text"
                      // 别让点击冒泡到整行，否则会顺便跳进会话页
                      onClick={(e) => e.stopPropagation()}
                      loading={del.isPending && del.variables === s.id}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Flex>
  );
}
