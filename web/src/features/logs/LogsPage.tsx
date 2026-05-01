import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Card,
  Select,
  Input,
  Button,
  Switch,
  Typography,
  Tag,
  Space,
  Spin,
  App,
  theme,
  Row,
  Col,
} from 'antd';
import { ReloadOutlined, SearchOutlined, FileTextOutlined } from '@ant-design/icons';
import api from '@/services/api';

const { Text } = Typography;

interface LogFile {
  name: string;
  size: number;
  modified: string;
}

interface LogLine {
  raw: string;
  timestamp: string;
  level: string;
  logger: string;
  module: string;
  func: string;
  lineno: number;
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: '#8b0000',
};

export default function LogsPage() {
  const { token } = theme.useToken();
  const { message } = App.useApp();

  const [files, setFiles] = useState<LogFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>('');
  const [lines, setLines] = useState<LogLine[]>([]);
  const [total, setTotal] = useState(0);
  const [shown, setShown] = useState(0);
  const [loading, setLoading] = useState(false);

  const [levelFilter, setLevelFilter] = useState<string>('');
  const [searchText, setSearchText] = useState('');
  const [moduleFilter, setModuleFilter] = useState('');
  const [linesCount, setLinesCount] = useState(500);
  const [tail, setTail] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchFiles = useCallback(async () => {
    try {
      const res = await api.get('/logs/files');
      const data: LogFile[] = res.data || [];
      setFiles(data);
      if (!selectedFile && data.length > 0) {
        setSelectedFile(data[0].name);
      }
    } catch {
      message.error('加载日志文件失败');
    }
  }, [selectedFile, message]);

  const fetchLogs = useCallback(async () => {
    if (!selectedFile) return;
    setLoading(true);
    try {
      const params: Record<string, string | number | boolean> = {
        file: selectedFile,
        lines: linesCount,
        tail,
      };
      if (levelFilter) params.level = levelFilter;
      if (searchText) params.search = searchText;
      if (moduleFilter) params.module = moduleFilter;
      const res = await api.get('/logs/view', { params });
      setLines(res.data.lines || []);
      setTotal(res.data.total || 0);
      setShown(res.data.shown || 0);
    } catch {
      message.error('加载日志失败');
    } finally {
      setLoading(false);
    }
  }, [selectedFile, linesCount, tail, levelFilter, searchText, moduleFilter, message]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(fetchLogs, 5000);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [autoRefresh, fetchLogs]);

  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={4} style={{ marginBottom: 16 }}>
        <FileTextOutlined style={{ marginRight: 8 }} />
        日志查看
      </Typography.Title>

      {/* Filters */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="middle">
          <Col xs={24} sm={12} md={4}>
            <Select
              showSearch
              value={selectedFile || undefined}
              onChange={(v) => setSelectedFile(v)}
              placeholder="选择日志文件"
              style={{ width: '100%' }}
              options={files.map((f) => ({
                value: f.name,
                label: `${f.name} (${(f.size / 1024).toFixed(0)} KB)`,
              }))}
            />
          </Col>
          <Col xs={12} sm={6} md={2}>
            <Select
              allowClear
              value={levelFilter || undefined}
              onChange={(v) => setLevelFilter(v || '')}
              placeholder="级别"
              style={{ width: '100%' }}
              options={['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((l) => ({
                value: l,
                label: l,
              }))}
            />
          </Col>
          <Col xs={12} sm={6} md={3}>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="搜索..."
            />
          </Col>
          <Col xs={12} sm={6} md={3}>
            <Input
              allowClear
              value={moduleFilter}
              onChange={(e) => setModuleFilter(e.target.value)}
              placeholder="模块..."
            />
          </Col>
          <Col xs={12} sm={6} md={2}>
            <Select
              value={linesCount}
              onChange={(v) => setLinesCount(v)}
              style={{ width: '100%' }}
              options={[100, 300, 500, 1000, 2000, 5000].map((n) => ({
                value: n,
                label: `${n} 条`,
              }))}
            />
          </Col>
          <Col xs={12} sm={6} md={2}>
            <Space>
              <Switch
                checked={tail}
                onChange={setTail}
                checkedChildren="末尾"
                unCheckedChildren="开头"
              />
            </Space>
          </Col>
          <Col xs={12} sm={6} md={2}>
            <Space>
              <span style={{ fontSize: 12, color: token.colorTextSecondary }}>自动</span>
              <Switch checked={autoRefresh} onChange={setAutoRefresh} size="small" />
            </Space>
          </Col>
          <Col xs={12} sm={6} md={2}>
            <Button icon={<ReloadOutlined />} onClick={fetchLogs} loading={loading}>
              刷新
            </Button>
          </Col>
        </Row>
      </Card>

      {/* Info bar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginBottom: 8,
          fontSize: 12,
          color: token.colorTextSecondary,
        }}
      >
        <span>
          显示 {shown} / {total} 条
        </span>
        {selectedFile && (
          <span>
            文件：{selectedFile}
            {files.find((f) => f.name === selectedFile) &&
              ` (${(files.find((f) => f.name === selectedFile)!.size / 1024).toFixed(0)} KB)`}
          </span>
        )}
      </div>

      {/* Log display */}
      <Card
        size="small"
        styles={{
          body: { padding: 0 },
        }}
      >
        <div
          ref={containerRef}
          style={{
            height: 'calc(100vh - 360px)',
            minHeight: 400,
            overflow: 'auto',
            background: token.colorBgElevated || '#1e1e1e',
            borderRadius: token.borderRadius,
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace",
            fontSize: 13,
            lineHeight: '22px',
          }}
        >
          {loading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin />
            </div>
          ) : lines.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: 40,
                color: token.colorTextSecondary,
              }}
            >
              暂无日志
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {lines.map((line, i) => (
                  <tr
                    key={i}
                    style={{
                      background:
                        line.level === 'ERROR' || line.level === 'CRITICAL'
                          ? 'rgba(255, 77, 79, 0.08)'
                          : line.level === 'WARNING'
                            ? 'rgba(250, 173, 20, 0.05)'
                            : 'transparent',
                    }}
                  >
                    <td
                      style={{
                        padding: '1px 8px',
                        whiteSpace: 'nowrap',
                        color: token.colorBgElevated ? token.colorTextTertiary : '#888',
                        userSelect: 'none',
                        textAlign: 'right',
                        width: 50,
                      }}
                    >
                      {i + 1}
                    </td>
                    <td
                      style={{
                        padding: '1px 4px',
                        whiteSpace: 'nowrap',
                        color: token.colorBgElevated ? token.colorTextTertiary : '#888',
                        userSelect: 'none',
                        width: 24,
                      }}
                    >
                      <Tag
                        style={{
                          fontSize: 11,
                          lineHeight: '16px',
                          padding: '0 4px',
                          margin: 0,
                        }}
                        color={LEVEL_COLORS[line.level] || 'default'}
                      >
                        {line.level?.[0] || '?'}
                      </Tag>
                    </td>
                    <td
                      style={{
                        padding: '1px 8px',
                        whiteSpace: 'nowrap',
                        color: token.colorBgElevated ? token.colorTextSecondary : '#999',
                        userSelect: 'none',
                        width: 180,
                      }}
                    >
                      {line.timestamp}
                    </td>
                    <td
                      style={{
                        padding: '1px 8px',
                        whiteSpace: 'nowrap',
                        color: token.colorBgElevated ? token.colorTextSecondary : '#999',
                        userSelect: 'none',
                        maxWidth: 120,
                      }}
                    >
                      <Text
                        ellipsis={{ tooltip: line.module || line.logger }}
                        style={{
                          fontSize: 12,
                          color: token.colorBgElevated ? token.colorTextSecondary : '#999',
                        }}
                      >
                        {line.module || line.logger}
                      </Text>
                    </td>
                    <td
                      style={{
                        padding: '1px 8px',
                        color: token.colorBgElevated ? token.colorText : '#eee',
                        wordBreak: 'break-all',
                      }}
                    >
                      {line.raw}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}
