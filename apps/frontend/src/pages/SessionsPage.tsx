/**
 * 历史会话（占位）。
 *
 * 为什么现在就加一个空页面？因为**只有两条路由，才能验证路由真的在工作** ——
 * 一条路由的话，你分不清是路由生效了还是压根没配。
 *
 * S5 会把这个页面填上：从后端读 JSONL 会话列表，点击进入 /sessions/:id。
 */
import { Empty, Flex, Typography } from "antd";

export default function SessionsPage() {
  return (
    <Flex vertical style={{ height: "100%", width: "100%" }}>
      <Flex
        align="center"
        gap={12}
        style={{
          padding: "12px 24px",
          borderBottom: "1px solid rgba(255,255,255,.08)",
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          历史会话
        </Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          S5 接入
        </Typography.Text>
      </Flex>

      <Flex flex={1} align="center" justify="center" style={{ padding: 24 }}>
        <Empty
          description={
            <Typography.Text type="secondary">
              还没有会话记录 —— S5 会把对话写进 JSONL 文件，这里就能读到了。
            </Typography.Text>
          }
        />
      </Flex>
    </Flex>
  );
}