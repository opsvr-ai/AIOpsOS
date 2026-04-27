import { Collapse, Tag, Typography, theme, Space } from 'antd';
import {
  LoadingOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  AimOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';

interface PlanStep {
  step: number;
  tool: string;
  args: Record<string, unknown>;
}

interface ToolResult {
  step: number;
  tool: string;
  output: string;
}

export function IntentCard({ message }: { message: string }) {
  const { token } = theme.useToken();

  return (
    <div
      style={{
        padding: '8px 14px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
        color: token.colorTextSecondary,
        flex: 1,
      }}
    >
      <AimOutlined style={{ color: token.colorPrimary, fontSize: 14, flexShrink: 0 }} />
      <span>{message}</span>
      <Tag
        color="processing"
        style={{ borderRadius: 6, fontSize: 11, lineHeight: '20px', flexShrink: 0 }}
      >
        意图识别
      </Tag>
    </div>
  );
}

export function PlanCard({ steps, execResults }: { steps: PlanStep[]; execResults: ToolResult[] }) {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const executed = new Set(execResults.map((r) => r.step));
  const hasFailures = execResults.some((r) => r.output.startsWith('Tool error'));

  const stepIcon = (s: PlanStep) => {
    const result = execResults.find((r) => r.step === s.step);
    if (result) {
      return result.output.startsWith('Tool error') ? (
        <CloseCircleOutlined style={{ color: token.colorError }} />
      ) : (
        <CheckCircleOutlined style={{ color: token.colorSuccess }} />
      );
    }
    if (executed.has(s.step)) {
      return <LoadingOutlined style={{ color: token.colorPrimary }} />;
    }
    return <ClockCircleOutlined style={{ color: token.colorTextTertiary }} />;
  };

  return (
    <div
      style={{
        margin: '4px auto',
        borderRadius: 10,
        background: mode === 'dark' ? token.colorFillQuaternary : token.colorFillSecondary,
        border: `1px solid ${token.colorBorderSecondary}`,
        overflow: 'hidden',
        maxWidth: 900,
      }}
    >
      <div style={{ padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <UnorderedListOutlined style={{ color: token.colorPrimary, fontSize: 14 }} />
        <Typography.Text style={{ color: token.colorText, fontSize: 13 }}>执行计划</Typography.Text>
        <Typography.Text style={{ color: token.colorTextTertiary, fontSize: 12 }}>
          · {steps.length} 步
        </Typography.Text>
        <div style={{ marginLeft: 'auto' }}>
          <Tag
            color={
              executed.size >= steps.length ? (hasFailures ? 'error' : 'success') : 'processing'
            }
            style={{ borderRadius: 6, fontSize: 11, lineHeight: '20px' }}
          >
            {executed.size}/{steps.length}
          </Tag>
        </div>
      </div>

      <Collapse
        ghost
        size="small"
        style={{ borderTop: `1px solid ${token.colorBorderSecondary}` }}
        items={steps.map((s) => {
          const result = execResults.find((r) => r.step === s.step);
          return {
            key: `${s.step}`,
            label: (
              <Space style={{ fontSize: 13, color: token.colorText }}>
                {stepIcon(s)}
                <span>
                  Step {s.step}: {s.tool}
                </span>
              </Space>
            ),
            children: (
              <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
                <div>
                  参数:{' '}
                  <code
                    style={{
                      color: token.colorText,
                      background: token.colorFillSecondary,
                      padding: '1px 6px',
                      borderRadius: 4,
                      fontSize: 12,
                    }}
                  >
                    {JSON.stringify(s.args)}
                  </code>
                </div>
                {result && (
                  <div style={{ marginTop: 8 }}>
                    结果:{' '}
                    <code
                      style={{
                        color: token.colorText,
                        background: token.colorFillSecondary,
                        padding: '1px 6px',
                        borderRadius: 4,
                        fontSize: 12,
                        display: 'inline-block',
                        marginTop: 4,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {result.output}
                    </code>
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
    </div>
  );
}
