import { useState, useCallback, useEffect } from 'react';
import {
  Table,
  Tag,
  Space,
  Typography,
  Input,
  Select,
  Button,
  Tabs,
  Card,
  Form,
  Modal,
  message,
  Descriptions,
  Divider,
  Collapse,
  Row,
  Col,
} from 'antd';
import {
  SearchOutlined,
  MessageOutlined,
  ReloadOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  CodeOutlined,
  QuestionCircleOutlined,
  ThunderboltOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface ItsmTicket {
  id: string;
  external_id: string | null;
  ticket_type: string;
  title: string;
  status: string;
  priority: string;
  affected_service: string;
  assigned_to: string | null;
  created_at: string | null;
  resolved_at: string | null;
  linked_alert_ids: string[] | null;
}

interface WorkflowRecord {
  id: string;
  source_system: string;
  workflow_id: string;
  action_type: string;
  title: string;
  status: string;
  payload: Record<string, unknown>;
  execute_script: string | null;
  execution_log: string | null;
  linked_ticket_id: string | null;
  linked_session_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface ScriptAnalysis {
  actions_detected: string[];
  input_fields: string[];
  output_fields: string[];
  script_type: string;
  guessed_itsm_system: string;
  suggested_config: Record<string, string>;
  test_command: string;
}

const TICKET_TYPE_LABELS: Record<string, string> = {
  incident: '事件单',
  change: '变更单',
  problem: '问题单',
  request: '服务请求',
  task: '任务单',
};

const PRIORITY_COLORS: Record<string, string> = {
  critical: 'red',
  high: 'orange',
  medium: 'blue',
  low: 'default',
};

const STATUS_COLORS: Record<string, string> = {
  open: 'blue',
  in_progress: 'orange',
  resolved: 'green',
  closed: 'default',
};

const ACTION_TYPE_LABELS: Record<string, string> = {
  create: '创建',
  update: '更新',
  close: '关闭',
  escalate: '升级',
  execute: '执行脚本',
};

// ── Ticket list tab ──

function TicketListTab() {
  const [tickets, setTickets] = useState<ItsmTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [service, setService] = useState('');
  const [ticketType, setTicketType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');
  const [selectedTicket, setSelectedTicket] = useState<ItsmTicket | null>(null);
  const [linkModalOpen, setLinkModalOpen] = useState(false);

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, page_size: 30 };
      if (service) params.service = service;
      if (ticketType) params.ticket_type = ticketType;
      if (status) params.status = status;
      if (keyword) params.keyword = keyword;
      const resp = await api.get('/itsm/tickets', { params });
      setTickets(resp.data.items ?? []);
      setTotal(resp.data.total ?? 0);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [page, service, ticketType, status, keyword]);

  const handleSearch = () => {
    setPage(1);
    fetchTickets();
  };

  const columns = [
    {
      title: '工单ID',
      dataIndex: 'external_id',
      key: 'external_id',
      width: 130,
      render: (t: string | null) => t ?? '-',
    },
    {
      title: '类型',
      dataIndex: 'ticket_type',
      key: 'ticket_type',
      width: 80,
      render: (t: string) => <Tag>{TICKET_TYPE_LABELS[t] || t}</Tag>,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (p: string) => <Tag color={PRIORITY_COLORS[p] || 'default'}>{p}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => <Tag color={STATUS_COLORS[s] || 'default'}>{s}</Tag>,
    },
    { title: '影响服务', dataIndex: 'affected_service', key: 'affected_service', width: 110 },
    {
      title: '关联告警',
      dataIndex: 'linked_alert_ids',
      key: 'linked_alert_ids',
      width: 90,
      render: (ids: string[] | null) => ((ids?.length ?? 0) > 0 ? `${ids!.length}条` : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, record: ItsmTicket) => (
        <Button
          type="link"
          size="small"
          icon={<LinkOutlined />}
          onClick={() => {
            setSelectedTicket(record);
            setLinkModalOpen(true);
          }}
        >
          联动
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          placeholder="服务名"
          allowClear
          style={{ width: 150 }}
          value={service}
          onChange={(e) => setService(e.target.value)}
        />
        <Select
          allowClear
          placeholder="工单类型"
          style={{ width: 120 }}
          value={ticketType}
          onChange={setTicketType}
          options={[
            { value: 'incident', label: '事件单' },
            { value: 'change', label: '变更单' },
            { value: 'problem', label: '问题单' },
            { value: 'request', label: '服务请求' },
          ]}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'open', label: '待处理' },
            { value: 'in_progress', label: '处理中' },
            { value: 'resolved', label: '已解决' },
            { value: 'closed', label: '已关闭' },
          ]}
        />
        <Input
          placeholder="关键词"
          allowClear
          style={{ width: 180 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
          查询
        </Button>
        <Button icon={<ReloadOutlined />} onClick={handleSearch} loading={loading}>
          刷新
        </Button>
      </Space>

      <Table
        dataSource={tickets}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="middle"
        pagination={{
          current: page,
          total,
          pageSize: 30,
          onChange: (p) => {
            setPage(p);
            fetchTickets();
          },
          showSizeChanger: false,
        }}
      />

      <LinkWorkflowModal
        ticket={selectedTicket}
        open={linkModalOpen}
        onClose={() => setLinkModalOpen(false)}
      />
    </div>
  );
}

// ── Link to workflow modal ──

function LinkWorkflowModal({
  ticket,
  open,
  onClose,
}: {
  ticket: ItsmTicket | null;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await api.post('/workflow/trigger', {
        ticket_type: ticket?.ticket_type || 'task',
        title: ticket?.title || values.title,
        description: values.description || ticket?.title || '',
        priority: ticket?.priority || 'medium',
        affected_service: ticket?.affected_service || '',
        datasource_config: values.datasource_config ? JSON.parse(values.datasource_config) : {},
        linked_ticket_id: ticket?.external_id || null,
      });
      message.success('联动任务已创建');
      onClose();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || '创建失败');
    }
    setSubmitting(false);
  };

  return (
    <Modal
      title="创建 ITSM 联动任务"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={submitting}
      width={560}
    >
      {ticket && (
        <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
          <Descriptions.Item label="工单">{ticket.external_id}</Descriptions.Item>
          <Descriptions.Item label="类型">
            {TICKET_TYPE_LABELS[ticket.ticket_type] || ticket.ticket_type}
          </Descriptions.Item>
          <Descriptions.Item label="标题" span={2}>
            {ticket.title}
          </Descriptions.Item>
        </Descriptions>
      )}
      <Form form={form} layout="vertical">
        <Form.Item name="title" label="任务标题" rules={[{ required: true }]}>
          <Input placeholder="联动任务名称" />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <TextArea rows={3} placeholder="任务描述（可选）" />
        </Form.Item>
        <Form.Item name="datasource_config" label="数据源配置 (JSON)">
          <TextArea rows={3} placeholder='{"itsm_system": "script", "api_url": "..."}' />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ── Workflow Panel Tab ──

function WorkflowPanelTab() {
  const [workflows, setWorkflows] = useState<WorkflowRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [scriptHelp, setScriptHelp] = useState('');
  const [scriptInput, setScriptInput] = useState('');
  const [analysis, setAnalysis] = useState<ScriptAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.get('/workflow', { params: { limit: 50 } });
      setWorkflows(resp.data.items ?? []);
      setTotal(resp.data.total ?? 0);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  const fetchScriptHelp = useCallback(async () => {
    try {
      const resp = await api.get('/workflow/script-help');
      setScriptHelp(resp.data.help || '');
    } catch {
      setScriptHelp('帮助文档暂时无法加载，请参考下方示例脚本格式。');
    }
  }, []);

  useEffect(() => {
    fetchWorkflows();
    fetchScriptHelp();
  }, [fetchWorkflows, fetchScriptHelp]);

  const handleAnalyzeScript = async () => {
    if (!scriptInput.trim()) return;
    setAnalyzing(true);
    try {
      const resp = await api.post('/workflow/analyze-script', { script_content: scriptInput });
      setAnalysis(resp.data);
      message.success('脚本分析完成');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || '分析失败');
    }
    setAnalyzing(false);
  };

  const workflowColumns = [
    {
      title: '任务ID',
      dataIndex: 'workflow_id',
      key: 'workflow_id',
      width: 130,
      render: (t: string) => t || '-',
    },
    {
      title: '类型',
      dataIndex: 'action_type',
      key: 'action_type',
      width: 80,
      render: (t: string) => <Tag>{ACTION_TYPE_LABELS[t] || t}</Tag>,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string) => (
        <Tag color={s === 'failed' ? 'red' : STATUS_COLORS[s] || 'default'}>{s}</Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : '-'),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : '-'),
    },
  ];

  return (
    <div>
      {/* Script Help */}
      <Collapse
        style={{ marginBottom: 20 }}
        items={[
          {
            key: 'help',
            label: (
              <span>
                <QuestionCircleOutlined style={{ marginRight: 8 }} />
                脚本适配器帮助文档 — 如何编写自定义 ITSM 对接脚本
              </span>
            ),
            children: (
              <pre
                style={{
                  fontSize: 12,
                  maxHeight: 500,
                  overflow: 'auto',
                  background: '#fafafa',
                  padding: 16,
                  borderRadius: 6,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {scriptHelp || '加载中...'}
              </pre>
            ),
          },
        ]}
      />

      <Row gutter={16}>
        {/* Script Analyzer */}
        <Col span={12}>
          <Card
            title={
              <span>
                <CodeOutlined style={{ marginRight: 6 }} />
                脚本分析与字段映射
              </span>
            }
            size="small"
          >
            <TextArea
              rows={8}
              placeholder="在此粘贴你的 ITSM 对接脚本（bash/python），平台将自动分析输入输出字段并生成目标报文格式映射..."
              value={scriptInput}
              onChange={(e) => setScriptInput(e.target.value)}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={handleAnalyzeScript}
              loading={analyzing}
              style={{ marginTop: 8 }}
              disabled={!scriptInput.trim()}
            >
              分析脚本
            </Button>

            {analysis && (
              <div style={{ marginTop: 16 }}>
                <Divider style={{ margin: '12px 0' }} />
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="检测到系统">
                    {analysis.guessed_itsm_system}
                  </Descriptions.Item>
                  <Descriptions.Item label="脚本类型">{analysis.script_type}</Descriptions.Item>
                  <Descriptions.Item label="支持动作" span={2}>
                    {analysis.actions_detected.map((a) => (
                      <Tag key={a} color="blue">
                        {a}
                      </Tag>
                    ))}
                  </Descriptions.Item>
                  <Descriptions.Item label="输入字段" span={2}>
                    {analysis.input_fields.map((f) => (
                      <Tag key={f}>{f}</Tag>
                    ))}
                  </Descriptions.Item>
                  <Descriptions.Item label="输出字段" span={2}>
                    {analysis.output_fields.map((f) => (
                      <Tag key={f} color="green">
                        {f}
                      </Tag>
                    ))}
                  </Descriptions.Item>
                </Descriptions>

                <Text strong style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
                  建议配置 (放入数据源 config):
                </Text>
                <pre
                  style={{
                    fontSize: 11,
                    background: '#f0f5ff',
                    padding: 8,
                    borderRadius: 4,
                    marginTop: 4,
                  }}
                >
                  {JSON.stringify(analysis.suggested_config, null, 2)}
                </pre>

                <Text strong style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
                  本地测试命令:
                </Text>
                <Paragraph
                  copyable
                  style={{ fontSize: 11, background: '#f5f5f5', padding: 8, borderRadius: 4 }}
                >
                  {analysis.test_command}
                </Paragraph>
              </div>
            )}
          </Card>
        </Col>

        {/* Quick Workflow Trigger */}
        <Col span={12}>
          <Card
            title={
              <span>
                <PlayCircleOutlined style={{ marginRight: 6 }} />
                快速创建联动任务
              </span>
            }
            size="small"
          >
            <QuickTriggerForm onSuccess={fetchWorkflows} />
          </Card>
        </Col>
      </Row>

      {/* Workflow History */}
      <Card
        title={
          <span>
            <HistoryOutlined style={{ marginRight: 6 }} />
            联动任务历史
          </span>
        }
        style={{ marginTop: 20 }}
        size="small"
      >
        <Table
          dataSource={workflows}
          columns={workflowColumns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ total, pageSize: 50, showSizeChanger: false }}
        />
      </Card>
    </div>
  );
}

function QuickTriggerForm({ onSuccess }: { onSuccess: () => void }) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      let config = {};
      if (values.datasource_config) {
        try {
          config = JSON.parse(values.datasource_config);
        } catch {
          message.error('数据源配置 JSON 格式错误');
          setSubmitting(false);
          return;
        }
      }
      await api.post('/workflow/trigger', {
        ticket_type: values.ticket_type,
        title: values.title,
        description: values.description || '',
        priority: values.priority || 'medium',
        affected_service: values.affected_service || '',
        datasource_config: config,
        execute_script: values.execute_script || null,
      });
      message.success('联动任务已创建');
      form.resetFields();
      onSuccess();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail || '创建失败');
    }
    setSubmitting(false);
  };

  return (
    <Form form={form} layout="vertical" size="small">
      <Form.Item
        name="ticket_type"
        label="工单类型"
        rules={[{ required: true }]}
        initialValue="task"
      >
        <Select
          options={[
            { value: 'incident', label: '事件单' },
            { value: 'change', label: '变更单' },
            { value: 'task', label: '任务单' },
            { value: 'request', label: '请求单' },
          ]}
        />
      </Form.Item>
      <Form.Item name="title" label="标题" rules={[{ required: true }]}>
        <Input placeholder="工单标题" />
      </Form.Item>
      <Form.Item name="description" label="描述">
        <TextArea rows={2} placeholder="工单描述" />
      </Form.Item>
      <Form.Item name="priority" label="优先级" initialValue="medium">
        <Select
          options={[
            { value: 'critical', label: '紧急' },
            { value: 'high', label: '高' },
            { value: 'medium', label: '中' },
            { value: 'low', label: '低' },
          ]}
        />
      </Form.Item>
      <Form.Item name="affected_service" label="影响服务">
        <Input placeholder="服务名称" />
      </Form.Item>
      <Form.Item name="datasource_config" label="数据源配置 (JSON)">
        <TextArea
          rows={2}
          placeholder='{"itsm_system": "script", "api_url": "https://..."}'
          style={{ fontFamily: 'monospace' }}
        />
      </Form.Item>
      <Form.Item name="execute_script" label="关联脚本 (可选)">
        <TextArea rows={3} placeholder="#!/bin/bash ..." style={{ fontFamily: 'monospace' }} />
      </Form.Item>
      <Button
        type="primary"
        icon={<LinkOutlined />}
        onClick={handleSubmit}
        loading={submitting}
        block
      >
        创建联动任务
      </Button>
    </Form>
  );
}

// ── Main Page ──

export default function ItsmPage() {
  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <MessageOutlined style={{ marginRight: 8 }} />
          ITSM 工单管理
        </Title>
      </div>

      <Tabs
        defaultActiveKey="tickets"
        items={[
          { key: 'tickets', label: '工单列表', children: <TicketListTab /> },
          { key: 'workflow', label: '流程联动', children: <WorkflowPanelTab /> },
        ]}
      />
    </div>
  );
}
