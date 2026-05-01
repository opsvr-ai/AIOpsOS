import { useState } from 'react';
import { Input, Segmented, Space, Typography } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

const { Text } = Typography;

interface Props {
  value?: string;
  onChange?: (value: string) => void;
}

const CRON_PRESETS: { label: string; value: string; desc: string }[] = [
  { label: '每分钟', value: '* * * * *', desc: '每分钟执行一次' },
  { label: '每5分钟', value: '*/5 * * * *', desc: '每5分钟执行一次' },
  { label: '每小时', value: '0 * * * *', desc: '每小时整点执行' },
  { label: '每天零点', value: '0 0 * * *', desc: '每天午夜执行' },
  { label: '每周一早9点', value: '0 9 * * 1', desc: '周一早上9点' },
  { label: '自定义', value: '__custom__', desc: '手动输入表达式' },
];

function previewNextRuns(expr: string, n: number = 5): string[] {
  try {
    const parts = expr.trim().split(/\s+/);
    if (parts.length < 5) return [];
    // Simple preview for common patterns
    const now = dayjs();
    const runs: string[] = [];
    let cursor = now;
    for (let i = 0; i < n && runs.length < n; i++) {
      if (expr === '* * * * *' || expr === '*/1 * * * *') {
        cursor = cursor.add(1, 'minute');
      } else if (expr.startsWith('*/')) {
        const interval = parseInt(expr.match(/\*\/(\d+)/)?.[1] || '5');
        cursor = cursor.add(interval, 'minute');
      } else if (expr === '0 * * * *') {
        cursor = cursor.add(1, 'hour').startOf('hour');
      } else if (expr === '0 0 * * *') {
        cursor = cursor.add(1, 'day').startOf('day');
      } else {
        break;
      }
      runs.push(cursor.format('MM-DD HH:mm'));
    }
    return runs;
  } catch {
    return [];
  }
}

export default function CronBuilder({ value = '', onChange }: Props) {
  const isPreset = CRON_PRESETS.slice(0, -1).some((p) => p.value === value);
  const [mode, setMode] = useState<string>(isPreset ? value : '__custom__');
  const [custom, setCustom] = useState(isPreset ? '' : value);
  const nextRuns = value ? previewNextRuns(value) : [];

  const handlePreset = (v: string) => {
    setMode(v);
    if (v === '__custom__') {
      onChange?.(custom);
    } else {
      setCustom('');
      onChange?.(v);
    }
  };

  return (
    <div>
      <Segmented
        block
        value={mode}
        onChange={(v) => handlePreset(v as string)}
        options={CRON_PRESETS.map((p) => ({
          label: p.label,
          value: p.value,
          title: p.desc,
        }))}
        style={{ marginBottom: 12 }}
      />

      {mode === '__custom__' && (
        <Input
          value={custom}
          onChange={(e) => {
            setCustom(e.target.value);
            onChange?.(e.target.value);
          }}
          placeholder="*/5 * * * *"
          style={{ fontFamily: 'monospace' }}
        />
      )}

      {nextRuns.length > 0 && (
        <Space style={{ marginTop: 8 }} size={4}>
          <ClockCircleOutlined style={{ color: '#8b8b8b', fontSize: 12 }} />
          <Text type="secondary" style={{ fontSize: 12 }}>
            下次执行:
          </Text>
          {nextRuns.map((t, i) => (
            <Text key={i} code style={{ fontSize: 11 }}>
              {t}
            </Text>
          ))}
        </Space>
      )}
    </div>
  );
}
