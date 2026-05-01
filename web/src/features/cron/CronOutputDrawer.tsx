import { Drawer, Typography } from 'antd';
import type { CronJobData } from './CronJobCard';

const { Text } = Typography;

interface Props {
  job: CronJobData | null;
  onClose: () => void;
}

export default function CronOutputDrawer({ job, onClose }: Props) {
  return (
    <Drawer
      title={job ? `${job.name} — 执行输出` : ''}
      open={!!job}
      onClose={onClose}
      width={640}
      destroyOnHidden
    >
      {job?.last_run && (
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 16 }}>
          最近执行: {new Date(job.last_run).toLocaleString('zh-CN')}
        </Text>
      )}

      {job?.last_output ? (
        <pre
          style={{
            background: '#1e1e1e',
            color: '#d4d4d4',
            padding: 16,
            borderRadius: 8,
            fontSize: 12,
            fontFamily: 'monospace',
            lineHeight: '20px',
            overflow: 'auto',
            maxHeight: 'calc(100vh - 180px)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {job.last_output}
        </pre>
      ) : (
        <Text type="secondary" style={{ fontStyle: 'italic' }}>
          暂无执行记录
        </Text>
      )}
    </Drawer>
  );
}
