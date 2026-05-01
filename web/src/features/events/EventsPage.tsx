import { useEffect, useState } from 'react';
import { Card, Table, Select, Typography, Tag, App, Spin, Empty, theme } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface DatasourceRef {
  id: string;
  name: string;
  source_type: string;
  table_mapping: Record<string, unknown>;
  last_ingested_at: string | null;
  total_ingested: number;
}

interface ColumnMeta {
  name: string;
  type: string;
  source_path?: string;
}

interface EventTableData {
  datasource_id: string;
  datasource_name: string;
  table_name: string;
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
  total: number;
  page: number;
  page_size: number;
}

const TYPE_TAG_COLOR: Record<string, string> = {
  string: 'blue',
  text: 'blue',
  integer: 'green',
  float: 'green',
  datetime: 'orange',
  json: 'purple',
};

export default function EventsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  const [datasources, setDatasources] = useState<DatasourceRef[]>([]);
  const [datasourcesLoading, setDatasourcesLoading] = useState(true);
  const [selectedDs, setSelectedDs] = useState<string | null>(null);
  const [eventData, setEventData] = useState<EventTableData | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  useEffect(() => {
    loadDatasources();
  }, []);

  useEffect(() => {
    if (selectedDs) {
      loadEvents();
    }
  }, [selectedDs, page, pageSize]);

  const loadDatasources = async () => {
    setDatasourcesLoading(true);
    try {
      const res = await api.get('/events/datasources');
      const dsList: DatasourceRef[] = res.data ?? [];
      setDatasources(dsList);
      if (dsList.length > 0 && !selectedDs) {
        setSelectedDs(dsList[0].id);
      }
    } catch {
      msg.error('加载数据源列表失败');
    } finally {
      setDatasourcesLoading(false);
    }
  };

  const loadEvents = async () => {
    if (!selectedDs) return;
    setEventsLoading(true);
    try {
      const res = await api.get(`/events/${selectedDs}`, {
        params: { page, page_size: pageSize },
      });
      setEventData(res.data);
    } catch {
      msg.error('加载事件数据失败');
      setEventData(null);
    } finally {
      setEventsLoading(false);
    }
  };

  const buildColumns = () => {
    if (!eventData) return [];
    return eventData.columns
      .filter((c) => c.name !== 'raw_event' && c.name !== 'id')
      .map((col) => ({
        title: (
          <span>
            {col.name}
            <Tag
              color={TYPE_TAG_COLOR[col.type] || 'default'}
              style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px' }}
            >
              {col.type}
            </Tag>
          </span>
        ),
        dataIndex: col.name,
        key: col.name,
        ellipsis: true,
        width: col.type === 'datetime' ? 180 : 150,
        render: (val: unknown) => {
          if (val === null || val === undefined)
            return <Typography.Text type="secondary">—</Typography.Text>;
          if (col.type === 'json' && typeof val === 'object') {
            return (
              <Typography.Paragraph
                ellipsis={{ rows: 2, expandable: true }}
                style={{ margin: 0, fontSize: 12, maxWidth: 300 }}
              >
                <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(val, null, 2)}
                </pre>
              </Typography.Paragraph>
            );
          }
          return String(val);
        },
      }));
  };

  const dsOptions = datasources.map((ds) => ({
    value: ds.id,
    label: `${ds.name} (${ds.total_ingested} 条)`,
  }));

  return (
    <div>
      <Typography.Title level={4} style={{ margin: '0 0 20px', fontWeight: 600 }}>
        事件数据
      </Typography.Title>

      {datasourcesLoading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : datasources.length === 0 ? (
        <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
          <Empty description="暂无配置表映射的数据源">
            <Typography.Text type="secondary">
              在数据源配置中添加 table_mapping 即可将事件写入自定义表结构
            </Typography.Text>
          </Empty>
        </Card>
      ) : (
        <>
          <Card
            size="small"
            style={{
              marginBottom: 16,
              borderRadius: 12,
              background: token.colorBgElevated ?? token.colorBgContainer,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <DatabaseOutlined style={{ color: token.colorPrimary, fontSize: 16 }} />
              <Typography.Text strong>数据源</Typography.Text>
              <Select
                value={selectedDs}
                onChange={(val) => {
                  setSelectedDs(val);
                  setPage(1);
                }}
                options={dsOptions}
                style={{ minWidth: 280 }}
              />
              {eventData && (
                <Typography.Text type="secondary" style={{ marginLeft: 'auto', fontSize: 13 }}>
                  共 {eventData.total} 条记录
                </Typography.Text>
              )}
            </div>
          </Card>

          <Card style={{ borderRadius: 12 }}>
            <Table
              loading={eventsLoading}
              dataSource={eventData?.rows ?? []}
              columns={buildColumns()}
              rowKey="id"
              size="small"
              scroll={{ x: 'max-content' }}
              pagination={{
                current: page,
                pageSize,
                total: eventData?.total ?? 0,
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 条`,
                onChange: (p, ps) => {
                  setPage(p);
                  if (ps !== pageSize) setPageSize(ps);
                },
              }}
              locale={{ emptyText: <Empty description="暂无数据" /> }}
            />
          </Card>
        </>
      )}
    </div>
  );
}
