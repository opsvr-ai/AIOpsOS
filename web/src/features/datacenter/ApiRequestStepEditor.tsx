import { Button, Input, Select, Space, Card, Row, Col } from 'antd';
import { PlusOutlined, DeleteOutlined, ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons';

const { TextArea } = Input;

export interface ApiRequestStep {
  step: number;
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string;
  extract: string;
  store_as: string;
}

interface Props {
  value?: ApiRequestStep[];
  onChange?: (steps: ApiRequestStep[]) => void;
}

const DEFAULT_STEP: ApiRequestStep = {
  step: 0,
  method: 'GET',
  url: '',
  headers: {},
  body: '',
  extract: '',
  store_as: '',
};

function parseJsonField(v: string, fallback: Record<string, string>): Record<string, string> {
  if (!v || !v.trim()) return fallback;
  try { return JSON.parse(v); } catch { return fallback; }
}

export default function ApiRequestStepEditor({ value = [], onChange }: Props) {
  const handleAdd = () => {
    const steps = [...value, { ...DEFAULT_STEP, step: value.length + 1 }];
    onChange?.(steps);
  };

  const handleRemove = (index: number) => {
    const steps = value.filter((_, i) => i !== index).map((s, i) => ({ ...s, step: i + 1 }));
    onChange?.(steps);
  };

  const handleMove = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= value.length) return;
    const steps = [...value];
    [steps[index], steps[target]] = [steps[target], steps[index]];
    steps.forEach((s, i) => { s.step = i + 1; });
    onChange?.(steps);
  };

  const handleChange = (index: number, field: string, val: unknown) => {
    const steps = value.map((s, i) => (i === index ? { ...s, [field]: val } : s));
    onChange?.(steps);
  };

  if (value.length === 0) {
    return (
      <Button type="dashed" block icon={<PlusOutlined />} onClick={handleAdd} style={{ marginTop: 8 }}>
        添加请求步骤
      </Button>
    );
  }

  return (
    <div>
      {value.map((step, i) => (
        <Card
          key={i}
          size="small"
          title={`步骤 ${i + 1}`}
          style={{ marginBottom: 10, borderRadius: 8 }}
          extra={
            <Space size={4}>
              <Button size="small" type="text" icon={<ArrowUpOutlined />} disabled={i === 0}
                onClick={() => handleMove(i, -1)} />
              <Button size="small" type="text" icon={<ArrowDownOutlined />} disabled={i === value.length - 1}
                onClick={() => handleMove(i, 1)} />
              <Button size="small" type="text" danger icon={<DeleteOutlined />}
                onClick={() => handleRemove(i)} />
            </Space>
          }
        >
          <Row gutter={[8, 8]}>
            <Col span={6}>
              <Select
                value={step.method}
                onChange={(v) => handleChange(i, 'method', v)}
                style={{ width: '100%' }}
                options={['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((m) => ({ label: m, value: m }))}
              />
            </Col>
            <Col span={18}>
              <Input
                value={step.url}
                onChange={(e) => handleChange(i, 'url', e.target.value)}
                placeholder="URL, 支持 {{var}} 模板"
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
            </Col>
            <Col span={12}>
              <TextArea
                rows={2}
                value={step.headers ? JSON.stringify(step.headers, null, 2) : ''}
                onChange={(e) => handleChange(i, 'headers', parseJsonField(e.target.value, {}))}
                placeholder='Headers (JSON) 如: {"Authorization": "Bearer {{token}}"}'
                style={{ fontFamily: 'monospace', fontSize: 11 }}
              />
            </Col>
            <Col span={12}>
              <TextArea
                rows={2}
                value={step.body}
                onChange={(e) => handleChange(i, 'body', e.target.value)}
                placeholder='Body (JSON), 仅 POST/PUT/PATCH'
                style={{ fontFamily: 'monospace', fontSize: 11 }}
              />
            </Col>
            <Col span={8}>
              <Input
                value={step.extract}
                onChange={(e) => handleChange(i, 'extract', e.target.value)}
                placeholder='JSONPath, 如 $.data[*]'
                style={{ fontFamily: 'monospace', fontSize: 11 }}
              />
            </Col>
            <Col span={4}>
              <Input
                value={step.store_as}
                onChange={(e) => handleChange(i, 'store_as', e.target.value)}
                placeholder='存为变量名'
                style={{ fontSize: 11 }}
              />
            </Col>
          </Row>
        </Card>
      ))}
      <Button type="dashed" block icon={<PlusOutlined />} onClick={handleAdd}>
        添加请求步骤
      </Button>
    </div>
  );
}
