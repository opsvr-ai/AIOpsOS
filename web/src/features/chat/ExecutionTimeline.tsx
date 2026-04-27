import { useState, useCallback } from 'react';
import { Typography, Tag, theme, Button, Drawer, App } from 'antd';
import {
  RobotOutlined,
  CodeOutlined,
  ApiOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CaretRightOutlined,
  CopyOutlined,
  CheckOutlined,
} from '@ant-design/icons';
import type { ExecutionStep } from '@/stores/chatStore';

const TYPE_KEYS: Record<string, { icon: React.ReactNode; label: string }> = {
  sub_agent: { icon: <RobotOutlined />, label: '子智能体' },
  skill: { icon: <CodeOutlined />, label: 'Skill' },
  mcp: { icon: <ApiOutlined />, label: 'MCP工具' },
  builtin: { icon: <ThunderboltOutlined />, label: '内置工具' },
  tool: { icon: <ToolOutlined />, label: '工具' },
  retrieval: { icon: <CodeOutlined />, label: '检索' },
};

export default function ExecutionTimeline({ steps }: { steps: ExecutionStep[] }) {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerContent, setDrawerContent] = useState<{ title: string; content: string }>({
    title: '',
    content: '',
  });
  const [copiedIds, setCopiedIds] = useState<Set<string>>(new Set());

  if (!steps || steps.length === 0) return null;

  const typeColorMap: Record<string, string> = {
    sub_agent: token.colorPrimary,
    skill: token.purple || '#7C3AED',
    mcp: token.colorWarning,
    builtin: token.colorInfo,
    tool: token.colorSuccess,
    retrieval: token.gold || '#FAAD14',
  };

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openDrawer = (title: string, content: string) => {
    setDrawerContent({ title, content });
    setDrawerOpen(true);
  };

  const handleCopyStep = useCallback(
    async (e: React.MouseEvent, stepId: string, text: string) => {
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(text);
        setCopiedIds((prev) => {
          const next = new Set(prev);
          next.add(stepId);
          return next;
        });
        setTimeout(() => {
          setCopiedIds((prev) => {
            const next = new Set(prev);
            next.delete(stepId);
            return next;
          });
        }, 2000);
        message.success('已复制');
      } catch {
        /* ignore */
      }
    },
    [message],
  );

  return (
    <>
      <style>{`
        @keyframes execSlideDown {
          from { opacity: 0; transform: translateY(-8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .exec-step-enter {
          animation: execSlideDown 0.25s ease-out both;
        }
        @keyframes execPulse {
          0%, 100% { box-shadow: 0 0 0 0 currentColor; }
          50%      { box-shadow: 0 0 4px 2px transparent; }
        }
      `}</style>
      <div style={{ width: '100%' }}>
        {steps.map((step, i) => {
          const cfg = TYPE_KEYS[step.type] || TYPE_KEYS.tool;
          const cfgColor = typeColorMap[step.type] || token.colorSuccess;
          const isLast = i === steps.length - 1;
          const isExpanded = expandedIds.has(step.id);
          const hasDetail = step.input || step.output;
          const isRunning = step.status === 'running';

          // Icon color based on status
          const iconBg = isRunning ? token.colorPrimary : cfgColor;

          return (
            <div
              key={step.id}
              className="exec-step-enter"
              style={{
                position: 'relative',
                paddingLeft: 28,
                marginBottom: isLast ? 0 : 0,
                animationDelay: `${i * 40}ms`,
              }}
            >
              {/* Timeline connector with arrow */}
              {!isLast && (
                <div
                  style={{
                    position: 'absolute',
                    left: 5,
                    top: 18,
                    bottom: -6,
                    width: 0,
                    borderLeft: `2px solid ${token.colorBorderSecondary}`,
                  }}
                />
              )}
              {!isLast && (
                <div
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 'calc(100% - 12px)',
                    width: 0,
                    height: 0,
                    borderLeft: '5px solid transparent',
                    borderRight: '5px solid transparent',
                    borderTop: `6px solid ${token.colorBorderSecondary}`,
                  }}
                />
              )}

              {/* Icon circle */}
              <div
                style={{
                  position: 'absolute',
                  left: -6,
                  top: 2,
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  background: iconBg,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 7,
                  color: '#fff',
                  zIndex: 1,
                  animation: isRunning ? 'avatarFastPulse 0.8s ease-in-out infinite' : undefined,
                }}
              >
                {isRunning ? (
                  <LoadingOutlined spin style={{ fontSize: 8, color: '#fff' }} />
                ) : step.status === 'error' ? (
                  <CloseCircleOutlined style={{ fontSize: 8 }} />
                ) : (
                  cfg.icon
                )}
              </div>

              <div
                style={{
                  marginBottom: isLast ? 0 : 12,
                  padding: '8px 12px',
                  borderRadius: 10,
                  background: token.colorBgElevated ?? token.colorBgContainer,
                  border: `1px solid ${token.colorBorderSecondary}`,
                  cursor: hasDetail ? 'pointer' : 'default',
                }}
                onClick={() => hasDetail && toggleExpand(step.id)}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 8,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      minWidth: 0,
                      flex: 1,
                    }}
                  >
                    <StatusIcon status={step.status} token={token} />
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        color: '#fff',
                        background: cfgColor,
                        borderRadius: 4,
                        padding: '0 5px',
                        lineHeight: '18px',
                        flexShrink: 0,
                        minWidth: 18,
                        textAlign: 'center',
                      }}
                    >
                      {step.stepNumber ?? i + 1}
                    </span>
                    <Tag
                      color={cfgColor}
                      style={{ borderRadius: 4, margin: 0, fontSize: 10, flexShrink: 0 }}
                    >
                      {cfg.label}
                    </Tag>
                    <Typography.Text
                      strong
                      style={{
                        fontSize: 13,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {step.name}
                    </Typography.Text>
                  </div>

                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {/* Copy entire step */}
                    <Button
                      type="text"
                      size="small"
                      icon={
                        copiedIds.has(`step-${step.id}`) ? (
                          <CheckOutlined style={{ color: token.colorSuccess }} />
                        ) : (
                          <CopyOutlined />
                        )
                      }
                      onClick={(e) =>
                        handleCopyStep(
                          e,
                          `step-${step.id}`,
                          `[步骤 ${step.stepNumber ?? i + 1}] ${step.name} (${step.status === 'done' ? '完成' : step.status === 'error' ? '失败' : '执行中'})\n输入: ${step.input || '(无)'}\n输出: ${step.output || '(无)'}`,
                        )
                      }
                      style={{ color: token.colorTextTertiary, fontSize: 11 }}
                    />
                    {hasDetail && (
                      <CaretRightOutlined
                        style={{
                          fontSize: 10,
                          color: token.colorTextTertiary,
                          transition: 'transform 0.2s',
                          transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                        }}
                      />
                    )}
                  </div>
                </div>

                {/* Quick peek at input/output when collapsed */}
                {!isExpanded && hasDetail && (
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: 11,
                      color: token.colorTextTertiary,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {step.output
                      ? `输出: ${step.output.slice(0, 80)}${step.output.length > 80 ? '...' : ''}`
                      : step.input
                        ? `输入: ${step.input.slice(0, 80)}${step.input.length > 80 ? '...' : ''}`
                        : ''}
                  </div>
                )}

                {isExpanded && hasDetail && (
                  <div style={{ marginTop: 8 }}>
                    {step.input && (
                      <div style={{ marginBottom: 6 }}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            marginBottom: 2,
                          }}
                        >
                          <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                            输入
                          </Typography.Text>
                          <Button
                            type="text"
                            size="small"
                            icon={
                              copiedIds.has(`in-${step.id}`) ? (
                                <CheckOutlined
                                  style={{ color: token.colorSuccess, fontSize: 10 }}
                                />
                              ) : (
                                <CopyOutlined style={{ fontSize: 10 }} />
                              )
                            }
                            onClick={(e) => handleCopyStep(e, `in-${step.id}`, step.input)}
                            style={{
                              color: token.colorTextTertiary,
                              fontSize: 10,
                              padding: '0 4px',
                              height: 20,
                            }}
                          />
                        </div>
                        <div
                          onClick={() => openDrawer(`${step.name} - 输入`, step.input)}
                          style={{
                            padding: '6px 8px',
                            borderRadius: 6,
                            background: token.colorFillSecondary,
                            fontSize: 11,
                            fontFamily: 'monospace',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                            maxHeight: 120,
                            overflowY: 'auto',
                            color: token.colorTextSecondary,
                            cursor: 'pointer',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = token.colorFillTertiary;
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = token.colorFillSecondary;
                          }}
                        >
                          {step.input}
                        </div>
                      </div>
                    )}
                    {step.output && (
                      <div>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            marginBottom: 2,
                          }}
                        >
                          <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                            输出
                          </Typography.Text>
                          <Button
                            type="text"
                            size="small"
                            icon={
                              copiedIds.has(`out-${step.id}`) ? (
                                <CheckOutlined
                                  style={{ color: token.colorSuccess, fontSize: 10 }}
                                />
                              ) : (
                                <CopyOutlined style={{ fontSize: 10 }} />
                              )
                            }
                            onClick={(e) => handleCopyStep(e, `out-${step.id}`, step.output)}
                            style={{
                              color: token.colorTextTertiary,
                              fontSize: 10,
                              padding: '0 4px',
                              height: 20,
                            }}
                          />
                        </div>
                        <div
                          onClick={() => openDrawer(`${step.name} - 输出`, step.output)}
                          style={{
                            padding: '6px 8px',
                            borderRadius: 6,
                            background: token.colorFillSecondary,
                            fontSize: 11,
                            fontFamily: 'monospace',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                            maxHeight: 120,
                            overflowY: 'auto',
                            color: token.colorTextSecondary,
                            cursor: 'pointer',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = token.colorFillTertiary;
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = token.colorFillSecondary;
                          }}
                        >
                          {step.output}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Detail drawer */}
      <Drawer
        title={drawerContent.title}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={520}
        styles={{
          body: {
            padding: '16px',
            fontFamily: 'monospace',
            fontSize: 13,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          },
        }}
      >
        {drawerContent.content}
      </Drawer>
    </>
  );
}

function StatusIcon({
  status,
  token,
}: {
  status: string;
  token: ReturnType<typeof theme.useToken>['token'];
}) {
  if (status === 'running')
    return <LoadingOutlined style={{ color: token.colorPrimary, fontSize: 13 }} spin />;
  if (status === 'error')
    return <CloseCircleOutlined style={{ color: token.colorError, fontSize: 13 }} />;
  return <CheckCircleOutlined style={{ color: token.colorSuccess, fontSize: 13 }} />;
}
