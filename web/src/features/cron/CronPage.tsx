import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Card,
  Button,
  Space,
  Typography,
  App,
  Empty,
  Input,
  Select,
  Row,
  Col,
  Skeleton,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import api from '@/services/api';
import CronJobCard, { type CronJobData } from './CronJobCard';
import CronOutputDrawer from './CronOutputDrawer';
import CreateCronWizard from './CreateCronWizard';

const SKELETON_COUNT = 8;

export default function CronPage() {
  const { message: msg } = App.useApp();
  const [jobs, setJobs] = useState<CronJobData[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<CronJobData | null>(null);
  const [saving, setSaving] = useState(false);
  const [outputDrawer, setOutputDrawer] = useState<CronJobData | null>(null);
  const [filterName, setFilterName] = useState('');
  const [debouncedName, setDebouncedName] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'enabled' | 'disabled'>('all');
  const nameTimerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    nameTimerRef.current = setTimeout(() => setDebouncedName(filterName.trim()), 300);
    return () => clearTimeout(nameTimerRef.current);
  }, [filterName]);

  const filteredJobs = jobs.filter((job) => {
    if (filterStatus === 'enabled' && !job.enabled) return false;
    if (filterStatus === 'disabled' && job.enabled) return false;
    if (debouncedName && !job.name.toLowerCase().includes(debouncedName.toLowerCase()))
      return false;
    return true;
  });

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/cron/jobs');
      setJobs(res.data ?? []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (job: CronJobData) => {
    setEditing(job);
    setModalOpen(true);
  };

  const handleSave = async (values: Record<string, unknown>) => {
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/cron/jobs/${editing.id}`, values);
        msg.success('更新成功');
      } else {
        await api.post('/cron/jobs', values);
        msg.success('创建成功');
      }
      setModalOpen(false);
      loadJobs();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/cron/jobs/${id}`);
      msg.success('删除成功');
      loadJobs();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '删除失败');
    }
  };

  const handleTrigger = async (id: string) => {
    try {
      await api.post(`/cron/jobs/${id}/trigger`);
      msg.success('已触发，将在下次轮询时执行');
      loadJobs();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '触发失败');
    }
  };

  const handleToggle = async (job: CronJobData) => {
    try {
      await api.patch(`/cron/jobs/${job.id}`, { enabled: !job.enabled });
      loadJobs();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '操作失败');
    }
  };

  const renderSkeletons = () => (
    <Row gutter={[12, 12]}>
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <Col key={i} xs={24} sm={12} lg={8} xl={6}>
          <Card size="small" style={{ borderRadius: 12 }}>
            <Skeleton active paragraph={{ rows: 2 }} />
          </Card>
        </Col>
      ))}
    </Row>
  );

  return (
    <div style={{ padding: 24, maxWidth: 1440, margin: '0 auto' }}>
      <style>{`
        @keyframes cronFadeInUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .cron-card-enter {
          animation: cronFadeInUp 300ms ease-out both;
        }
        @media (prefers-reduced-motion: reduce) {
          .cron-card-enter { animation: none; }
        }
      `}</style>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            定时任务
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            管理周期性 AI 任务，支持 Cron 表达式和间隔调度
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadJobs}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            创建任务
          </Button>
        </Space>
      </div>

      {/* Filter bar */}
      {!loading && jobs.length > 0 && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
          <Input
            placeholder="搜索名称..."
            prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
            value={filterName}
            onChange={(e) => setFilterName(e.target.value)}
            allowClear
            style={{ width: 260 }}
          />
          <Select
            value={filterStatus}
            onChange={(v) => setFilterStatus(v)}
            options={[
              { value: 'all', label: `全部 (${jobs.length})` },
              { value: 'enabled', label: `启用 (${jobs.filter((j) => j.enabled).length})` },
              { value: 'disabled', label: `停用 (${jobs.filter((j) => !j.enabled).length})` },
            ]}
            style={{ width: 150 }}
          />
        </div>
      )}

      {/* Content */}
      {loading ? (
        renderSkeletons()
      ) : filteredJobs.length === 0 ? (
        <Empty
          description={jobs.length === 0 ? '暂无定时任务' : '没有匹配的任务'}
          style={{ paddingTop: 80 }}
        >
          {jobs.length === 0 ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              创建第一个任务
            </Button>
          ) : (
            <Button
              onClick={() => {
                setFilterName('');
                setFilterStatus('all');
              }}
            >
              清除筛选
            </Button>
          )}
        </Empty>
      ) : (
        <Row gutter={[12, 12]}>
          {filteredJobs.map((job, i) => (
            <Col key={job.id} xs={24} sm={12} lg={8} xl={6}>
              <CronJobCard
                index={i}
                job={job}
                onToggle={handleToggle}
                onTrigger={handleTrigger}
                onEdit={openEdit}
                onDelete={handleDelete}
                onViewOutput={(j) => setOutputDrawer(j)}
              />
            </Col>
          ))}
        </Row>
      )}

      {/* Create/Edit Wizard */}
      <CreateCronWizard
        open={modalOpen}
        editing={editing}
        saving={saving}
        onCancel={() => setModalOpen(false)}
        onSave={handleSave}
      />

      {/* Output Drawer */}
      <CronOutputDrawer job={outputDrawer} onClose={() => setOutputDrawer(null)} />
    </div>
  );
}
