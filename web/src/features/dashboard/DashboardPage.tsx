import React, { useEffect, useMemo, useState } from "react";
import {
  Card,
  Col,
  Progress,
  Row,
  Spin,
  Tag,
  Timeline,
  Typography,
  theme,
} from "antd";
import {
  AlertOutlined,
  ApiOutlined,
  ArrowRightOutlined,
  BookOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  HddOutlined,
  MessageOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import api from "@/services/api";

interface DashboardData {
  system_status: {
    overall: string;
    online_agents: number;
    active_sessions: number;
    last_ingestion: string | null;
  };
  alerts_summary: {
    critical: number;
    warning: number;
    info: number;
    total: number;
  };
  agents_summary: { total: number; online: number };
  sessions_summary: { active: number; sleeping: number; unconsolidated: number };
  cron_summary: { total: number; enabled: number };
  knowledge_summary: { documents: number; memories: number };
  data_pipeline: {
    cmdb: { nodes: number; pending_reviews: number };
    datasources: { total: number; enabled: number };
    itsm: { total: number; open: number };
  };
  recent_activities: Array<{
    kind: string;
    title: string;
    severity?: string;
    time: string;
  }>;
}

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  healthy: { color: "#22C55E", label: "正常" },
  warning: { color: "#F59E0B", label: "警告" },
  critical: { color: "#DC2626", label: "异常" },
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#DC2626",
  warning: "#F59E0B",
  info: "#3B82F6",
};

const ACTIVITY_ICONS: Record<string, React.ReactNode> = {
  alert: <AlertOutlined />,
  consolidation: <BookOutlined />,
  cmdb_sync: <CloudServerOutlined />,
};

export default function DashboardPage() {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/dashboard/summary")
      .then((res) => setData(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const status = data?.system_status;
  const totalAlerts = data?.alerts_summary.total ?? 0;

  const alertPercent = useMemo(() => {
    if (!data || totalAlerts === 0) return null;
    const t = data.alerts_summary;
    return {
      critical: (t.critical / totalAlerts) * 100,
      warning: (t.warning / totalAlerts) * 100,
      info: (t.info / totalAlerts) * 100,
    };
  }, [data, totalAlerts]);

  const timeAgo = (iso: string | null) => {
    if (!iso) return "暂无";
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins}分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}小时前`;
    return `${Math.floor(hours / 24)}天前`;
  };

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  const pipelineRows = [
    {
      label: "CMDB",
      icon: <HddOutlined />,
      ok: (data?.data_pipeline.cmdb.nodes ?? 0) > 0,
      detail: `${data?.data_pipeline.cmdb.nodes ?? 0} 节点${data?.data_pipeline.cmdb.pending_reviews ? `，${data.data_pipeline.cmdb.pending_reviews} 待审核` : ""}`,
      path: "/ops/cmdb",
    },
    {
      label: "数据源",
      icon: <DatabaseOutlined />,
      ok: (data?.data_pipeline.datasources.enabled ?? 0) > 0,
      detail: `${data?.data_pipeline.datasources.enabled ?? 0} 启用 / ${data?.data_pipeline.datasources.total ?? 0} 总数`,
      path: "/ops/datacenter",
    },
    {
      label: "ITSM",
      icon: <ApiOutlined />,
      ok: (data?.data_pipeline.itsm.total ?? 0) > 0 && (data?.data_pipeline.itsm.open ?? 0) <= 10,
      warn: (data?.data_pipeline.itsm.open ?? 0) > 10,
      detail: `${data?.data_pipeline.itsm.open ?? 0} 待处理 / ${data?.data_pipeline.itsm.total ?? 0} 工单`,
      path: "/ops/itsm",
    },
    {
      label: "知识库",
      icon: <BookOutlined />,
      ok: (data?.knowledge_summary.documents ?? 0) > 0,
      detail: `${data?.knowledge_summary.documents ?? 0} 文档，${data?.knowledge_summary.memories ?? 0} 记忆`,
      path: "/ai/knowledge",
    },
  ];

  const actionGroups = [
    {
      group: "事件响应",
      items: [
        { label: "告警中心", path: "/ops/alerts", icon: <AlertOutlined /> },
        { label: "事件接入", path: "/ops/events", icon: <DatabaseOutlined /> },
        { label: "CMDB 查询", path: "/ops/cmdb", icon: <HddOutlined /> },
        { label: "日志检索", path: "/ops/logs", icon: <FileTextOutlined /> },
      ],
    },
    {
      group: "AI 协作",
      items: [
        { label: "新对话", path: "/ops/chat", icon: <MessageOutlined /> },
        { label: "知识库", path: "/ai/knowledge", icon: <BookOutlined /> },
        { label: "记忆管理", path: "/ai/memory", icon: <BookOutlined /> },
        { label: "智能体", path: "/ai/agents", icon: <RobotOutlined /> },
      ],
    },
    {
      group: "配置管理",
      items: [
        { label: "数据接入", path: "/ops/datacenter", icon: <ApiOutlined /> },
        { label: "定时任务", path: "/ai/cron", icon: <ClockCircleOutlined /> },
        { label: "自动化", path: "/ops/automation", icon: <ThunderboltOutlined /> },
        { label: "工具市场", path: "/ai/tools", icon: <ToolOutlined /> },
      ],
    },
  ];

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 24, fontWeight: 600 }}>
        运维总览
      </Typography.Title>

      {/* ── ① System Status Bar ── */}
      <Card
        style={{ borderRadius: 12, marginBottom: 16 }}
        styles={{ body: { padding: "10px 20px" } }}
      >
        <Row align="middle" gutter={24}>
          <Col>
            <Tag
              color={STATUS_MAP[status?.overall || "healthy"]?.color}
              style={{ fontSize: 14, padding: "2px 12px", borderRadius: 6 }}
            >
              {STATUS_MAP[status?.overall || "healthy"]?.label}
            </Tag>
          </Col>
          <Col>
            <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>
              <RobotOutlined style={{ marginRight: 4 }} />
              {status?.online_agents ?? 0} 智能体在线
            </span>
          </Col>
          <Col>
            <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>
              <MessageOutlined style={{ marginRight: 4 }} />
              {status?.active_sessions ?? 0} 活跃会话
            </span>
          </Col>
          <Col flex="auto" style={{ textAlign: "right" }}>
            <span style={{ color: token.colorTextTertiary, fontSize: 12 }}>
              数据最后接入: {timeAgo(status?.last_ingestion ?? null)}
            </span>
          </Col>
        </Row>
      </Card>

      {/* ── ② Alert Summary + ③ AI Ops Status ── */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {/* ② Alert Summary */}
        <Col xs={24} md={12}>
          <Card style={{ borderRadius: 12, height: "100%" }} styles={{ body: { padding: 20 } }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 16 }}>
              告警概览
            </Typography.Text>

            {totalAlerts === 0 ? (
              <div style={{ textAlign: "center", padding: "24px 0", color: token.colorTextTertiary }}>
                <AlertOutlined style={{ fontSize: 32, marginBottom: 8, color: "#22C55E" }} />
                <div>暂无告警</div>
              </div>
            ) : (
              <Row align="middle" gutter={24}>
                <Col>
                  <Progress
                    type="circle"
                    percent={100}
                    size={90}
                    format={() => totalAlerts}
                    strokeColor={{
                      "0%": SEVERITY_COLOR.critical,
                      [`${alertPercent?.critical ?? 0}%`]: SEVERITY_COLOR.critical,
                      [`${alertPercent?.critical ?? 0}%`]: SEVERITY_COLOR.warning,
                      [`${(alertPercent?.critical ?? 0) + (alertPercent?.warning ?? 0)}%`]:
                        SEVERITY_COLOR.warning,
                      [`${(alertPercent?.critical ?? 0) + (alertPercent?.warning ?? 0)}%`]:
                        SEVERITY_COLOR.info,
                    }}
                  />
                </Col>
                <Col flex="auto">
                  {(["critical", "warning", "info"] as const).map((level) => {
                    const count = data?.alerts_summary[level] ?? 0;
                    return (
                      <div key={level} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                        <span
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            background: SEVERITY_COLOR[level],
                            flexShrink: 0,
                          }}
                        />
                        <span style={{ fontSize: 13, flex: 1 }}>
                          {level === "critical" ? "严重" : level === "warning" ? "警告" : "信息"}
                        </span>
                        <span style={{ fontSize: 18, fontWeight: 600 }}>{count}</span>
                      </div>
                    );
                  })}
                </Col>
              </Row>
            )}

            <a onClick={() => navigate("/ops/alerts")} style={{ fontSize: 12, display: "block", marginTop: 16 }}>
              查看告警中心 <ArrowRightOutlined />
            </a>
          </Card>
        </Col>

        {/* ③ AI Ops Status */}
        <Col xs={24} md={12}>
          <Card style={{ borderRadius: 12, height: "100%" }} styles={{ body: { padding: 20 } }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 16 }}>
              AI 运营状态
            </Typography.Text>
            <Row gutter={[0, 16]}>
              {[
                {
                  icon: <RobotOutlined />,
                  label: "智能体",
                  value: data?.agents_summary.online ?? 0,
                  total: data?.agents_summary.total ?? 0,
                  color: "#22C55E",
                },
                {
                  icon: <MessageOutlined />,
                  label: "活跃会话",
                  value: data?.sessions_summary.active ?? 0,
                  total: (data?.sessions_summary.active ?? 0) + (data?.sessions_summary.sleeping ?? 0),
                  suffix: `休眠 ${data?.sessions_summary.sleeping ?? 0}`,
                  color: "#3B82F6",
                },
                {
                  icon: <BookOutlined />,
                  label: "待巩固记忆",
                  value: data?.sessions_summary.unconsolidated ?? 0,
                  color: "#7C3AED",
                },
                {
                  icon: <ClockCircleOutlined />,
                  label: "定时任务",
                  value: data?.cron_summary.enabled ?? 0,
                  total: data?.cron_summary.total ?? 0,
                  color: "#D97706",
                },
              ].map((row) => (
                <Col span={24} key={row.label}>
                  <Row justify="space-between" align="middle">
                    <span style={{ fontSize: 13 }}>
                      <span style={{ marginRight: 6, color: row.color }}>{row.icon}</span>
                      {row.label}
                    </span>
                    <span style={{ fontSize: 13, color: token.colorTextSecondary }}>
                      {row.value}
                      {row.total !== undefined && ` / ${row.total}`}
                      {row.suffix && ` (${row.suffix})`}
                    </span>
                  </Row>
                  {row.total !== undefined && row.total > 0 && (
                    <Progress
                      percent={(row.value / row.total) * 100}
                      showInfo={false}
                      size="small"
                      strokeColor={row.color}
                      style={{ marginBottom: 0 }}
                    />
                  )}
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>

      {/* ── ④ Data Pipeline + ⑤ Recent Activities ── */}
      <Row gutter={[16, 16]}>
        {/* ④ Data Pipeline Status */}
        <Col xs={24} md={12}>
          <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 20 } }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 16 }}>
              数据接入管道
            </Typography.Text>
            {pipelineRows.map((pipe) => {
              const dotColor = pipe.ok
                ? "#22C55E"
                : pipe.warn
                  ? "#F59E0B"
                  : token.colorTextTertiary;
              return (
                <Row
                  key={pipe.label}
                  align="middle"
                  style={{ marginBottom: 10, cursor: "pointer" }}
                  onClick={() => navigate(pipe.path)}
                >
                  <Col style={{ width: 28 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        display: "inline-block",
                        background: dotColor,
                      }}
                    />
                  </Col>
                  <Col style={{ width: 36, color: token.colorTextSecondary }}>{pipe.icon}</Col>
                  <Col style={{ width: 68, fontSize: 13, fontWeight: 500 }}>{pipe.label}</Col>
                  <Col flex="auto" style={{ fontSize: 12, color: token.colorTextSecondary }}>
                    {pipe.detail}
                  </Col>
                  <Col>
                    <ArrowRightOutlined style={{ fontSize: 10, color: token.colorTextTertiary }} />
                  </Col>
                </Row>
              );
            })}
          </Card>
        </Col>

        {/* ⑤ Recent Activities */}
        <Col xs={24} md={12}>
          <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 20 } }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 16 }}>
              最近活动
            </Typography.Text>
            {(data?.recent_activities?.length ?? 0) === 0 ? (
              <div style={{ textAlign: "center", padding: "24px 0", color: token.colorTextTertiary }}>
                暂无活动
              </div>
            ) : (
              <Timeline
                items={data?.recent_activities?.map((a) => ({
                  dot: ACTIVITY_ICONS[a.kind] ? (
                    <span
                      style={{
                        color: a.severity ? SEVERITY_COLOR[a.severity] : token.colorPrimary,
                        fontSize: 12,
                      }}
                    >
                      {ACTIVITY_ICONS[a.kind]}
                    </span>
                  ) : undefined,
                  color: a.severity ? SEVERITY_COLOR[a.severity] : undefined,
                  children: (
                    <div>
                      <div style={{ fontSize: 13 }}>{a.title}</div>
                      <div style={{ fontSize: 11, color: token.colorTextTertiary, marginTop: 2 }}>
                        {new Date(a.time).toLocaleString("zh-CN")}
                      </div>
                    </div>
                  ),
                })) ?? []}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* ── ⑥ Quick Actions ── */}
      <Card style={{ borderRadius: 12, marginTop: 16 }} styles={{ body: { padding: 20 } }}>
        <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 16 }}>
          快速操作
        </Typography.Text>
        <Row gutter={[16, 12]}>
          {actionGroups.map((group) => (
            <Col xs={24} sm={8} key={group.group}>
              <div
                style={{
                  background: token.colorFillTertiary,
                  borderRadius: 10,
                  padding: "12px 14px",
                }}
              >
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 11, display: "block", marginBottom: 8, textTransform: "uppercase" }}
                >
                  {group.group}
                </Typography.Text>
                <Row gutter={[4, 4]}>
                  {group.items.map((item) => (
                    <Col span={12} key={item.path}>
                      <a
                        onClick={() => navigate(item.path)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: 13,
                          padding: "4px 6px",
                          borderRadius: 6,
                          transition: "background 0.2s",
                        }}
                        onMouseEnter={(e) => {
                          (e.currentTarget as HTMLElement).style.background = token.colorFillSecondary;
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLElement).style.background = "transparent";
                        }}
                      >
                        <span style={{ fontSize: 13, color: token.colorPrimary }}>{item.icon}</span>
                        {item.label}
                      </a>
                    </Col>
                  ))}
                </Row>
              </div>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
