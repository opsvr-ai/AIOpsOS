import { useEffect, useState, useCallback } from 'react';
import {
  Drawer,
  Button,
  List,
  Typography,
  Tag,
  Input,
  Select,
  DatePicker,
  message,
  Empty,
  Popconfirm,
  Space,
  Progress,
  Tooltip,
} from 'antd';
import {
  CheckOutlined,
  EditOutlined,
  CloseOutlined,
  PlusOutlined,
  DeleteOutlined,
  UnorderedListOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '@/services/api';

interface FormField {
  name: string;
  label: string;
  type: string;
  required?: boolean;
  options?: string[];
}

interface Task {
  id: string;
  session_id: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  source: string;
  confidence: number | null;
  due_date: string | null;
  form_definition: FormField[] | null;
  form_data: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

interface Props {
  open: boolean;
  onClose: () => void;
  sessionId: string | null;
}

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理', color: 'default' },
  { value: 'pending_review', label: '待确认', color: 'warning' },
  { value: 'in_progress', label: '进行中', color: 'processing' },
  { value: 'completed', label: '已完成', color: 'success' },
  { value: 'cancelled', label: '已取消', color: 'error' },
];

const PRIORITY_OPTIONS: Record<string, { label: string; color: string }> = {
  low: { label: '低', color: 'green' },
  medium: { label: '中', color: 'blue' },
  high: { label: '高', color: 'orange' },
  critical: { label: '紧急', color: 'red' },
};

export default function TaskPanel({ open, onClose, sessionId }: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<Task>>({});
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [formDataMap, setFormDataMap] = useState<Record<string, Record<string, unknown>>>({});

  const fetchTasks = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await api.get(`/sessions/${sessionId}/tasks`);
      const taskList: Task[] = res.data || [];
      setTasks(taskList);
      const fdm: Record<string, Record<string, unknown>> = {};
      for (const t of taskList) {
        if (t.form_data) fdm[t.id] = t.form_data;
      }
      setFormDataMap(fdm);
    } catch {
      // session might not exist yet
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (open && sessionId) fetchTasks();
  }, [open, sessionId, fetchTasks]);

  const handleSaveEdit = async (taskId: string) => {
    try {
      await api.patch(`/sessions/${sessionId}/tasks/${taskId}`, editForm);
      setEditingId(null);
      setEditForm({});
      await fetchTasks();
      message.success('任务已更新');
    } catch {
      message.error('更新失败');
    }
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditForm({});
  };

  const handleStatusChange = async (taskId: string, newStatus: string) => {
    try {
      await api.patch(`/sessions/${sessionId}/tasks/${taskId}`, { status: newStatus });
      await fetchTasks();
    } catch {
      message.error('更新失败');
    }
  };

  const handleApprove = async (taskId: string) => {
    try {
      const fd = formDataMap[taskId] || {};
      await api.patch(`/sessions/${sessionId}/tasks/${taskId}`, {
        status: 'pending',
        form_data: fd,
      });
      await fetchTasks();
      message.success('任务已确认');
    } catch {
      message.error('操作失败');
    }
  };

  const handleReject = async (taskId: string) => {
    try {
      await api.patch(`/sessions/${sessionId}/tasks/${taskId}`, { status: 'cancelled' });
      await fetchTasks();
      message.success('任务已拒绝');
    } catch {
      message.error('操作失败');
    }
  };

  const handleFormFieldChange = (taskId: string, field: string, value: unknown) => {
    setFormDataMap((prev) => ({
      ...prev,
      [taskId]: { ...(prev[taskId] || {}), [field]: value },
    }));
  };

  const handleDelete = async (taskId: string) => {
    try {
      await api.delete(`/sessions/${sessionId}/tasks/${taskId}`);
      await fetchTasks();
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  };

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      await api.post(`/sessions/${sessionId}/tasks`, { title: newTitle.trim() });
      setNewTitle('');
      setCreating(false);
      await fetchTasks();
      message.success('任务已创建');
    } catch {
      message.error('创建失败');
    }
  };

  const startEdit = (task: Task) => {
    setEditingId(task.id);
    setEditForm({
      title: task.title,
      description: task.description,
      status: task.status,
      priority: task.priority,
      due_date: task.due_date,
    });
  };

  const confidenceColor = (v: number) => {
    if (v >= 0.8) return '#22c55e';
    if (v >= 0.5) return '#f59e0b';
    return '#ef4444';
  };

  return (
    <Drawer
      title={
        <Space>
          <UnorderedListOutlined />
          对话任务
        </Space>
      }
      open={open}
      onClose={onClose}
      width={400}
      styles={{ body: { padding: 0 } }}
      extra={
        <Button
          type="primary"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setCreating(!creating)}
        >
          新建
        </Button>
      }
    >
      {creating && (
        <div style={{ padding: '12px 24px', borderBottom: '1px solid #f0f0f0' }}>
          <Input
            placeholder="任务标题..."
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onPressEnter={handleCreate}
            autoFocus
            suffix={
              <Space size={4}>
                <Button type="text" size="small" icon={<CheckOutlined />} onClick={handleCreate} />
                <Button
                  type="text"
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={() => {
                    setCreating(false);
                    setNewTitle('');
                  }}
                />
              </Space>
            }
          />
        </div>
      )}

      {tasks.length === 0 && !loading ? (
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Empty description="暂无任务">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              对话结束后 AI 将自动提取任务，或点击上方按钮手动创建
            </Typography.Text>
          </Empty>
        </div>
      ) : (
        <List
          loading={loading}
          dataSource={tasks}
          style={{ padding: 0 }}
          renderItem={(task) => (
            <List.Item
              style={{
                padding: '12px 24px',
                borderBottom: '1px solid #f0f0f0',
                flexDirection: 'column',
                alignItems: 'stretch',
              }}
            >
              {editingId === task.id ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
                  <Input
                    value={editForm.title || ''}
                    onChange={(e) => setEditForm((f) => ({ ...f, title: e.target.value }))}
                    placeholder="标题"
                  />
                  <Input.TextArea
                    value={editForm.description || ''}
                    onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                    placeholder="描述"
                    rows={2}
                  />
                  <Space wrap>
                    <Select
                      size="small"
                      value={editForm.priority || 'medium'}
                      onChange={(v) => setEditForm((f) => ({ ...f, priority: v }))}
                      style={{ width: 90 }}
                      options={Object.entries(PRIORITY_OPTIONS).map(([k, v]) => ({
                        value: k,
                        label: v.label,
                      }))}
                    />
                    <Select
                      size="small"
                      value={editForm.status || 'pending'}
                      onChange={(v) => setEditForm((f) => ({ ...f, status: v }))}
                      style={{ width: 100 }}
                      options={STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                    />
                    <DatePicker
                      size="small"
                      value={editForm.due_date ? dayjs(editForm.due_date) : null}
                      onChange={(d) => setEditForm((f) => ({ ...f, due_date: d?.toISOString() }))}
                      placeholder="截止日期"
                    />
                  </Space>
                  <Space style={{ justifyContent: 'flex-end' }}>
                    <Button size="small" onClick={handleCancelEdit}>
                      取消
                    </Button>
                    <Button size="small" type="primary" onClick={() => handleSaveEdit(task.id)}>
                      保存
                    </Button>
                  </Space>
                </div>
              ) : (
                <div style={{ width: '100%' }}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      justifyContent: 'space-between',
                      gap: 8,
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}
                      >
                        {task.status === 'completed' && (
                          <span style={{ color: '#22c55e', fontSize: 12 }}>&#10003;</span>
                        )}
                        <Typography.Text
                          strong
                          delete={task.status === 'cancelled'}
                          style={{
                            ...(task.status === 'completed' ? { color: '#999' } : {}),
                            wordBreak: 'break-word',
                          }}
                        >
                          {task.title}
                        </Typography.Text>
                      </div>
                      {task.description && (
                        <Typography.Paragraph
                          type="secondary"
                          style={{ fontSize: 12, marginBottom: 6 }}
                          ellipsis={{ rows: 2 }}
                        >
                          {task.description}
                        </Typography.Paragraph>
                      )}
                      <Space size={4} wrap>
                        <Tag
                          color={PRIORITY_OPTIONS[task.priority]?.color || 'blue'}
                          style={{ fontSize: 11, lineHeight: '18px' }}
                        >
                          {PRIORITY_OPTIONS[task.priority]?.label || task.priority}
                        </Tag>
                        <Tag
                          color={
                            STATUS_OPTIONS.find((s) => s.value === task.status)?.color || 'default'
                          }
                          style={{ fontSize: 11, lineHeight: '18px' }}
                        >
                          {STATUS_OPTIONS.find((s) => s.value === task.status)?.label ||
                            task.status}
                        </Tag>
                        {task.source === 'extracted' && (
                          <Tag
                            icon={<RobotOutlined />}
                            color="geekblue"
                            style={{ fontSize: 11, lineHeight: '18px' }}
                          >
                            AI 提取
                          </Tag>
                        )}
                        {task.due_date && (
                          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                            {dayjs(task.due_date).format('MM/DD')}
                          </Typography.Text>
                        )}
                      </Space>

                      {/* Confidence score for extracted tasks */}
                      {task.source === 'extracted' && task.confidence != null && (
                        <Tooltip title={`置信度 ${Math.round(task.confidence * 100)}%`}>
                          <div
                            style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 6 }}
                          >
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              置信度
                            </Typography.Text>
                            <Progress
                              percent={Math.round(task.confidence * 100)}
                              size="small"
                              style={{ flex: 1, maxWidth: 120, margin: 0 }}
                              strokeColor={confidenceColor(task.confidence)}
                              showInfo={false}
                            />
                            <Typography.Text
                              style={{ fontSize: 11, color: confidenceColor(task.confidence) }}
                            >
                              {Math.round(task.confidence * 100)}%
                            </Typography.Text>
                          </div>
                        </Tooltip>
                      )}

                      {/* Inline form for extracted tasks */}
                      {task.form_definition &&
                        task.form_definition.length > 0 &&
                        task.status !== 'completed' &&
                        task.status !== 'cancelled' && (
                          <div
                            style={{
                              marginTop: 8,
                              padding: '8px 10px',
                              background: '#fafafa',
                              borderRadius: 6,
                              border: '1px solid #f0f0f0',
                            }}
                          >
                            {task.form_definition.map((field) => (
                              <div key={field.name} style={{ marginBottom: 6 }}>
                                <Typography.Text
                                  style={{ fontSize: 11, display: 'block', marginBottom: 2 }}
                                >
                                  {field.label}
                                  {field.required && <span style={{ color: '#ff4d4f' }}> *</span>}
                                </Typography.Text>
                                {field.type === 'select' && field.options ? (
                                  <Select
                                    size="small"
                                    style={{ width: '100%' }}
                                    placeholder={`选择${field.label}`}
                                    value={formDataMap[task.id]?.[field.name]}
                                    onChange={(v) => handleFormFieldChange(task.id, field.name, v)}
                                    options={field.options.map((o) => ({ value: o, label: o }))}
                                  />
                                ) : field.type === 'date' ? (
                                  <DatePicker
                                    size="small"
                                    style={{ width: '100%' }}
                                    placeholder={`选择${field.label}`}
                                    value={
                                      formDataMap[task.id]?.[field.name]
                                        ? dayjs(formDataMap[task.id]?.[field.name] as string)
                                        : null
                                    }
                                    onChange={(d) =>
                                      handleFormFieldChange(task.id, field.name, d?.toISOString())
                                    }
                                  />
                                ) : (
                                  <Input
                                    size="small"
                                    placeholder={`输入${field.label}`}
                                    value={(formDataMap[task.id]?.[field.name] as string) || ''}
                                    onChange={(e) =>
                                      handleFormFieldChange(task.id, field.name, e.target.value)
                                    }
                                  />
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                    </div>

                    <Space size={2} style={{ flexShrink: 0 }}>
                      {/* Approve/Reject for pending_review extracted tasks */}
                      {task.status === 'pending_review' ? (
                        <>
                          <Tooltip title="确认任务">
                            <Button
                              type="text"
                              size="small"
                              icon={<CheckOutlined />}
                              style={{ color: '#22c55e' }}
                              onClick={() => handleApprove(task.id)}
                            />
                          </Tooltip>
                          <Tooltip title="拒绝任务">
                            <Popconfirm
                              title="确定拒绝此任务？"
                              onConfirm={() => handleReject(task.id)}
                              okText="拒绝"
                              cancelText="取消"
                            >
                              <Button type="text" size="small" danger icon={<CloseOutlined />} />
                            </Popconfirm>
                          </Tooltip>
                        </>
                      ) : (
                        <>
                          <Select
                            size="small"
                            value={task.status}
                            onChange={(v) => handleStatusChange(task.id, v)}
                            style={{ width: 24 }}
                            variant="borderless"
                            dropdownStyle={{ minWidth: 90 }}
                            options={STATUS_OPTIONS.map((o) => ({
                              value: o.value,
                              label: o.label,
                            }))}
                          />
                          <Button
                            type="text"
                            size="small"
                            icon={<EditOutlined />}
                            onClick={() => startEdit(task)}
                          />
                          <Popconfirm
                            title="确定删除此任务？"
                            onConfirm={() => handleDelete(task.id)}
                            okText="删除"
                            cancelText="取消"
                          >
                            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                          </Popconfirm>
                        </>
                      )}
                    </Space>
                  </div>
                </div>
              )}
            </List.Item>
          )}
        />
      )}
    </Drawer>
  );
}
