import { Typography, Descriptions, Tag } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';

const { Text, Paragraph, Title } = Typography;

interface Props {
  analysisResult: Record<string, unknown>;
}

export default function AnalysisResultView({ analysisResult }: Props) {
  if (!analysisResult || !analysisResult.summary) {
    return <Text type="secondary">暂无分析结果</Text>;
  }

  const triggers = (analysisResult.triggers as string[]) || [];
  const analyzedAt = analysisResult.analyzed_at as string;

  return (
    <div>
      <Title level={5} style={{ marginTop: 0 }}>AI 分析结果</Title>
      <Paragraph>{analysisResult.summary as string}</Paragraph>
      {triggers.length > 0 && (
        <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
          <Descriptions.Item label="匹配规则">
            {triggers.map((t) => (
              <Tag key={t} color="blue">{t.slice(0, 8)}</Tag>
            ))}
          </Descriptions.Item>
          {analyzedAt && (
            <Descriptions.Item label="分析时间">
              <ClockCircleOutlined style={{ marginRight: 4 }} />
              {new Date(analyzedAt).toLocaleString('zh-CN')}
            </Descriptions.Item>
          )}
        </Descriptions>
      )}
    </div>
  );
}
