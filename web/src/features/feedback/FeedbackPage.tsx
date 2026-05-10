import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Tabs,
  Form,
  Input,
  Button,
  Table,
  Tag,
  Rate,
  App,
  Space,
  Typography,
  Empty,
  Select,
  theme,
  Tooltip,
  Skeleton,
} from 'antd';
import {
  BugOutlined,
  BulbOutlined,
  SendOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  StopOutlined,
  RocketOutlined,
  CodeOutlined,
  LineChartOutlined,
} from '@ant-design/icons';
import api from '@/services/api';
import { useThemeStore } from '@/stores/themeStore';
import ImageUploader, { type PendingImage } from './ImageUploader';
import FeedbackImageGallery from './FeedbackImageGallery';

interface FeedbackItem {
  id: string;
  user_id: string;
  username: string;
  type: string;
  title: string;
  description: string;
  status: string;
  rating: number | null;
  ai_analysis: string | null;
  resolved_version: string | null;
  images: string[];
  created_at: string;
  updated_at: string;
}

const STATUS_META: Record<string, { color: string; icon: React.ReactNode }> = {
  待AI分析: { color: 'default', icon: <ClockCircleOutlined /> },
  已分析: { color: 'blue', icon: <LineChartOutlined /> },
  开发中: { color: 'processing', icon: <CodeOutlined /> },
  修复中: { color: 'orange', icon: <SyncOutlined spin /> },
  驳回: { color: 'red', icon: <StopOutlined /> },
  已修复: { color: 'green', icon: <CheckCircleOutlined /> },
  开发完成: { color: 'cyan', icon: <CheckCircleOutlined /> },
  已上线: { color: 'purple', icon: <RocketOutlined /> },
};

const TYPE_CARDS = [
  {
    value: 'bug',
    icon: <BugOutlined style={{ fontSize: 32 }} />,
    title: 'Bug 缺陷',
    desc: '功能异常、界面问题、性能故障等',
    color: '#ff4d4f',
  },
  {
    value: 'feature',
    icon: <BulbOutlined style={{ fontSize: 32 }} />,
    title: '功能需求',
    desc: '新功能建议、体验优化、改进想法',
    color: '#1677ff',
  },
];

export default function FeedbackPage() {
  const { message: msg, modal } = App.useApp();
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const [form] = Form.useForm();
  const [feedbacks, setFeedbacks] = useState<FeedbackItem[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [selectedType, setSelectedType] = useState<string>('bug');
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);

  const loadFeedbacks = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { limit: 200, offset: 0 };
      if (typeFilter) params.type = typeFilter;
      if (statusFilter) params.status = statusFilter;
      const res = await api.get('/feedbacks', { params });
      setFeedbacks(res.data ?? []);
    } catch {
      // silently ignore
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter]);

  const loadStatuses = useCallback(async () => {
    try {
      const res = await api.get('/feedbacks/statuses');
      setStatuses((res.data ?? []).map((s: { value: string }) => s.value));
    } catch {
      /* */
    }
  }, []);

  useEffect(() => {
    loadFeedbacks();
    loadStatuses();
  }, [loadFeedbacks, loadStatuses]);

  /**
   * Uploads a single image to the server.
   * @param image - The pending image to upload
   * @returns The uploaded URL on success, or throws an error on failure
   */
  const uploadImage = async (image: PendingImage): Promise<string> => {
    const formData = new FormData();
    formData.append('file', image.file);
    
    const response = await api.post('/feedbacks/images', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    
    return response.data.url;
  };

  /**
   * Uploads all pending images and returns their URLs.
   * Updates image status during upload (uploading -> uploaded/error).
   * @returns Array of uploaded image URLs
   * @throws Error if any image upload fails
   */
  const uploadAllImages = async (): Promise<string[]> => {
    if (pendingImages.length === 0) {
      return [];
    }

    const uploadedUrls: string[] = [];
    const updatedImages = [...pendingImages];

    for (let i = 0; i < pendingImages.length; i++) {
      const image = pendingImages[i];
      
      // Update status to uploading
      updatedImages[i] = { ...image, status: 'uploading' };
      setPendingImages([...updatedImages]);

      try {
        const url = await uploadImage(image);
        uploadedUrls.push(url);
        
        // Update status to uploaded
        updatedImages[i] = { ...image, status: 'uploaded', uploadedUrl: url };
        setPendingImages([...updatedImages]);
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        const errorMessage = detail || '上传失败';
        
        // Update status to error
        updatedImages[i] = { ...image, status: 'error', error: errorMessage };
        setPendingImages([...updatedImages]);
        
        throw new Error(`图片上传失败: ${errorMessage}`);
      }
    }

    return uploadedUrls;
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      // Upload all pending images first
      const imageUrls = await uploadAllImages();
      
      // Submit feedback with image URLs
      await api.post('/feedbacks', { ...values, type: selectedType, images: imageUrls });
      msg.success('反馈已提交，感谢您的贡献！');
      form.resetFields();
      setPendingImages([]);
      loadFeedbacks();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      const errorMessage = (err as Error)?.message;
      msg.error(detail || errorMessage || '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleStatusChange = async (id: string, status: string) => {
    try {
      await api.patch(`/feedbacks/${id}`, { status });
      msg.success('状态已更新');
      loadFeedbacks();
    } catch {
      msg.error('更新失败');
    }
  };

  const handleRatingChange = async (id: string, rating: number) => {
    try {
      await api.patch(`/feedbacks/${id}`, { rating });
      msg.success('评分已更新');
      loadFeedbacks();
    } catch {
      msg.error('评分失败');
    }
  };

  const columns = [
    {
      title: '类型',
      dataIndex: 'type',
      width: 72,
      render: (t: string) =>
        t === 'bug' ? (
          <Tag icon={<BugOutlined />} color="red">
            Bug
          </Tag>
        ) : (
          <Tag icon={<BulbOutlined />} color="blue">
            需求
          </Tag>
        ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      width: 180,
      ellipsis: true,
      render: (t: string) => (
        <Typography.Text strong style={{ fontSize: 13 }}>
          {t}
        </Typography.Text>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      width: 200,
      ellipsis: true,
      render: (d: string, record: FeedbackItem) => (
        <Typography.Link
          onClick={() =>
            modal.info({
              title: '反馈详情',
              content: (
                <div>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{d}</div>
                  {record.images && record.images.length > 0 && (
                    <FeedbackImageGallery images={record.images} />
                  )}
                </div>
              ),
              width: 520,
            })
          }
        >
          {d.slice(0, 60)}
          {d.length > 60 ? '…' : ''}
        </Typography.Link>
      ),
    },
    { title: '反馈人', dataIndex: 'username', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (s: string, record: FeedbackItem) => (
        <Select
          size="small"
          value={s}
          style={{ width: 108 }}
          onChange={(val) => handleStatusChange(record.id, val)}
          options={statuses.map((st) => {
            const meta = STATUS_META[st];
            return {
              value: st,
              label: (
                <Space size={4}>
                  {meta?.icon}
                  <span>{st}</span>
                </Space>
              ),
            };
          })}
        />
      ),
    },
    {
      title: '评分',
      dataIndex: 'rating',
      width: 150,
      render: (r: number | null, record: FeedbackItem) => (
        <Rate
          value={r || 0}
          count={5}
          style={{ fontSize: 14 }}
          onChange={(val) => handleRatingChange(record.id, val)}
        />
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 130,
      render: (d: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {new Date(d).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </Typography.Text>
      ),
    },
  ];

  return (
    <div style={{ padding: '4px 0' }}>
      <Tabs
        size="large"
        items={[
          {
            key: 'submit',
            label: (
              <Space>
                <SendOutlined />
                <span>提交反馈</span>
              </Space>
            ),
            children: (
              <Card
                bordered={false}
                style={{
                  boxShadow: 'none',
                  background: token.colorFillAlter,
                  borderRadius: token.borderRadiusLG,
                }}
              >
                <div style={{ marginBottom: 24 }}>
                  <Typography.Text
                    type="secondary"
                    style={{ display: 'block', marginBottom: 12, fontSize: 13 }}
                  >
                    选择反馈类型
                  </Typography.Text>
                  <div style={{ display: 'flex', gap: 16 }}>
                    {TYPE_CARDS.map((card) => {
                      const active = selectedType === card.value;
                      return (
                        <div
                          key={card.value}
                          onClick={() => {
                            setSelectedType(card.value);
                            form.setFieldValue('type', card.value);
                          }}
                          style={{
                            flex: 1,
                            padding: '20px 16px',
                            borderRadius: token.borderRadiusLG,
                            border: `2px solid ${active ? card.color : token.colorBorder}`,
                            background: active
                              ? mode === 'dark'
                                ? `${card.color}15`
                                : `${card.color}08`
                              : token.colorBgContainer,
                            cursor: 'pointer',
                            transition: 'all 0.2s ease',
                            textAlign: 'center',
                          }}
                          onMouseEnter={(e) => {
                            if (!active) {
                              e.currentTarget.style.borderColor = card.color;
                              e.currentTarget.style.background =
                                mode === 'dark' ? `${card.color}08` : `${card.color}04`;
                            }
                          }}
                          onMouseLeave={(e) => {
                            if (!active) {
                              e.currentTarget.style.borderColor = token.colorBorder;
                              e.currentTarget.style.background = token.colorBgContainer;
                            }
                          }}
                        >
                          <div
                            style={{
                              color: active ? card.color : token.colorTextTertiary,
                              marginBottom: 8,
                            }}
                          >
                            {card.icon}
                          </div>
                          <div
                            style={{
                              fontWeight: 600,
                              fontSize: 15,
                              color: active ? card.color : token.colorText,
                              marginBottom: 4,
                            }}
                          >
                            {card.title}
                          </div>
                          <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
                            {card.desc}
                          </div>
                          {active && (
                            <div
                              style={{
                                width: 20,
                                height: 20,
                                borderRadius: '50%',
                                background: card.color,
                                color: '#fff',
                                display: 'inline-flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                fontSize: 12,
                                marginTop: 8,
                              }}
                            >
                              ✓
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                <Form form={form} layout="vertical" initialValues={{ type: 'bug' }}>
                  <Form.Item name="type" hidden>
                    <Input />
                  </Form.Item>
                  <Form.Item
                    name="title"
                    label={<Typography.Text strong>标题</Typography.Text>}
                    rules={[{ required: true, message: '请输入标题' }]}
                  >
                    <Input
                      placeholder="简要描述你遇到的问题或需求"
                      maxLength={256}
                      size="large"
                      prefix={
                        selectedType === 'bug' ? (
                          <BugOutlined style={{ color: '#ff4d4f' }} />
                        ) : (
                          <BulbOutlined style={{ color: '#1677ff' }} />
                        )
                      }
                    />
                  </Form.Item>
                  <Form.Item
                    name="description"
                    label={<Typography.Text strong>详细描述</Typography.Text>}
                    rules={[{ required: true, message: '请输入详细描述' }]}
                    extra={
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {selectedType === 'bug'
                          ? '请描述复现步骤、预期行为和实际结果'
                          : '请描述使用场景、期望功能和解决什么问题'}
                      </Typography.Text>
                    }
                  >
                    <Input.TextArea
                      rows={5}
                      placeholder={
                        selectedType === 'bug'
                          ? '1. 复现步骤：...\n2. 预期行为：...\n3. 实际结果：...'
                          : '1. 使用场景：...\n2. 期望功能：...\n3. 解决的问题：...'
                      }
                      style={{ fontSize: 14 }}
                    />
                  </Form.Item>
                  <Form.Item
                    label={<Typography.Text strong>截图/图片</Typography.Text>}
                  >
                    <ImageUploader
                      images={pendingImages}
                      onChange={setPendingImages}
                      maxCount={5}
                      maxSizeMB={5}
                      disabled={submitting}
                    />
                  </Form.Item>
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    loading={submitting}
                    onClick={handleSubmit}
                    size="large"
                    style={{ minWidth: 140 }}
                  >
                    提交反馈
                  </Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'list',
            label: (
              <Space>
                <ReloadOutlined />
                <span>反馈管理</span>
              </Space>
            ),
            children: (
              <>
                <Space style={{ marginBottom: 16 }} size="middle">
                  <Select
                    allowClear
                    placeholder="类型筛选"
                    style={{ width: 120 }}
                    value={typeFilter}
                    onChange={setTypeFilter}
                    options={[
                      { value: 'bug', label: 'Bug' },
                      { value: 'feature', label: '需求' },
                    ]}
                  />
                  <Select
                    allowClear
                    placeholder="状态筛选"
                    style={{ width: 140 }}
                    value={statusFilter}
                    onChange={setStatusFilter}
                    options={statuses.map((s) => ({
                      value: s,
                      label: (
                        <Space size={4}>
                          {STATUS_META[s]?.icon}
                          <span>{s}</span>
                        </Space>
                      ),
                    }))}
                  />
                  <Tooltip title="刷新列表">
                    <Button icon={<ReloadOutlined />} onClick={loadFeedbacks} loading={loading} />
                  </Tooltip>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    共 {feedbacks.length} 条
                  </Typography.Text>
                </Space>

                {loading && feedbacks.length === 0 ? (
                  <Skeleton active paragraph={{ rows: 8 }} />
                ) : (
                  <Table
                    rowKey="id"
                    dataSource={feedbacks}
                    columns={columns}
                    loading={loading}
                    scroll={{ x: 960 }}
                    size="middle"
                    locale={{
                      emptyText: (
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description={
                            <span style={{ color: token.colorTextTertiary }}>
                              暂无反馈记录，快来提交第一条吧
                            </span>
                          }
                        />
                      ),
                    }}
                    pagination={{
                      pageSize: 20,
                      showSizeChanger: false,
                      showTotal: (total) => `共 ${total} 条`,
                    }}
                    style={{ background: token.colorBgContainer }}
                  />
                )}
              </>
            ),
          },
        ]}
      />
    </div>
  );
}
