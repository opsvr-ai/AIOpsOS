import { theme } from 'antd';
import { useThemeStore } from '@/stores/themeStore';
import type { ExecutionStep } from '@/stores/chatStore';

const TYPE_LABELS: Record<string, string> = {
  sub_agent: '子智能体',
  skill: 'Skill',
  mcp: 'MCP工具',
  builtin: '内置工具',
  tool: '工具',
  retrieval: '检索',
};

// Design-spec colours from screen-2-preview.html §4 (light theme)
const SPEC = {
  light: {
    done: {
      rowBg: '#f6ffed',
      rowBorder: '#b7eb8f',
      tagBg: '#f6ffed',
      tagText: '#52c41a',
      tagBorder: '#b7eb8f',
    },
    running: {
      rowBg: '#e6f4ff',
      rowBorder: '#91caff',
      tagBg: '#e6f4ff',
      tagText: '#1677ff',
      tagBorder: '#91caff',
    },
    error: {
      rowBg: '#fff2f0',
      rowBorder: '#ffccc7',
      tagBg: '#fff2f0',
      tagText: '#ff4d4f',
      tagBorder: '#ffccc7',
    },
    pending: {
      rowBg: '#fafafa',
      rowBorder: '#e8e8e8',
      tagBg: '#f5f5f5',
      tagText: '#999',
      tagBorder: '#e8e8e8',
    },
  },
  dark: {
    done: {
      rowBg: 'rgba(82,196,26,0.12)',
      rowBorder: 'rgba(82,196,26,0.28)',
      tagBg: 'rgba(82,196,26,0.18)',
      tagText: '#52c41a',
      tagBorder: 'rgba(82,196,26,0.35)',
    },
    running: {
      rowBg: 'rgba(22,119,255,0.14)',
      rowBorder: 'rgba(22,119,255,0.32)',
      tagBg: 'rgba(22,119,255,0.2)',
      tagText: '#4096ff',
      tagBorder: 'rgba(22,119,255,0.4)',
    },
    error: {
      rowBg: 'rgba(255,77,79,0.12)',
      rowBorder: 'rgba(255,77,79,0.28)',
      tagBg: 'rgba(255,77,79,0.18)',
      tagText: '#ff7875',
      tagBorder: 'rgba(255,77,79,0.35)',
    },
    pending: {
      rowBg: 'rgba(255,255,255,0.04)',
      rowBorder: 'rgba(255,255,255,0.08)',
      tagBg: 'rgba(255,255,255,0.06)',
      tagText: '#999',
      tagBorder: 'rgba(255,255,255,0.1)',
    },
  },
} as const;

function palette(isDark: boolean) {
  return isDark ? SPEC.dark : SPEC.light;
}

export default function ExecutionTimeline({ steps }: { steps: ExecutionStep[] }) {
  const { token } = theme.useToken();
  const isDark = useThemeStore((s) => s.mode) === 'dark';

  if (!steps || steps.length === 0) return null;

  const doneCount = steps.filter((s) => s.status === 'done' || s.status === 'error').length;
  const allDone = doneCount === steps.length;
  const pal = palette(isDark);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  };

  const rowColors = (status: string) => {
    if (status === 'done') return pal.done;
    if (status === 'error') return pal.error;
    if (status === 'running') return pal.running;
    return pal.pending;
  };

  const statusLabel = (status: string) => {
    if (status === 'running') return '执行中';
    if (status === 'error') return '失败';
    return '等待中';
  };

  return (
    <>
      <style>{`
        @keyframes execStepIn {
          from { opacity: 0; transform: translateX(-4px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        .exec-step-enter {
          animation: execStepIn 0.2s ease-out both;
        }
        @keyframes execPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
        .exec-pulse {
          animation: execPulse 1.2s infinite;
        }
      `}</style>

      <div style={{ width: '100%' }}>
        {/* Progress bar row */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginBottom: 8,
            fontSize: 11,
            color: token.colorTextTertiary,
          }}
        >
          <div
            style={{
              flex: 1,
              height: 3,
              background: token.colorFillSecondary,
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${steps.length > 0 ? (doneCount / steps.length) * 100 : 0}%`,
                height: '100%',
                background: allDone ? token.colorSuccess : token.colorPrimary,
                borderRadius: 2,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
          <span>
            {doneCount}/{steps.length} 步骤
          </span>
        </div>

        {/* Step rows */}
        {steps.map((step, i) => {
          const status = step.status;
          const isRunning = status === 'running';
          const c = rowColors(status);
          const label = TYPE_LABELS[step.type] || '工具';

          return (
            <div
              key={step.id}
              className="exec-step-enter"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '7px 10px',
                borderRadius: 7,
                fontSize: 12,
                marginBottom: i === steps.length - 1 ? 0 : 4,
                background: c.rowBg,
                border: `1px solid ${c.rowBorder}`,
                transition: 'all 0.2s ease',
                animationDelay: `${i * 40}ms`,
              }}
            >
              {/* Status indicator — exact emojis from design spec */}
              <span
                style={{
                  fontSize: 14,
                  flexShrink: 0,
                  width: 18,
                  textAlign: 'center',
                  lineHeight: 1,
                }}
              >
                {isRunning ? (
                  <span className="exec-pulse" style={{ display: 'inline-block' }}>
                    ⚙
                  </span>
                ) : status === 'error' ? (
                  <span style={{ color: c.tagText }}>✗</span>
                ) : status === 'done' ? (
                  <span style={{ color: c.tagText }}>✓</span>
                ) : (
                  <span style={{ color: token.colorTextQuaternary }}>○</span>
                )}
              </span>

              {/* Type tag */}
              <span
                style={{
                  fontSize: 10,
                  padding: '1px 6px',
                  borderRadius: 3,
                  fontWeight: 600,
                  flexShrink: 0,
                  background: c.tagBg,
                  color: c.tagText,
                  border: `1px solid ${c.tagBorder}`,
                }}
              >
                {label}
              </span>

              {/* Tool name */}
              <span
                style={{
                  flex: 1,
                  fontSize: 12,
                  fontWeight: 500,
                  color: status === 'running' ? pal.running.tagText : token.colorText,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {step.name}
              </span>

              {/* Timestamp / status text */}
              <span
                style={{
                  fontSize: 10,
                  color: status === 'running' ? pal.running.tagText : token.colorTextTertiary,
                  flexShrink: 0,
                  marginLeft: 'auto',
                  fontWeight: status === 'running' ? 500 : 400,
                }}
              >
                {step.timestamp ? formatTime(step.timestamp) : statusLabel(status)}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}
