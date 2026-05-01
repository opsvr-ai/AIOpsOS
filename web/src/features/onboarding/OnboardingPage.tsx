import { useEffect, useState } from 'react';
import { Card, Typography, theme } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import TechBackground from '@/components/ui/TechBackground';
import Step1ModelConfig from './Step1ModelConfig';
import Step2Ldap from './Step2Ldap';
import Step3Channels from './Step3Channels';

const FADE_IN_DURATION = 1000;
const FADE_OUT_DURATION = 800;

const fadeKeyframes = `
@keyframes obFadeIn {
  0% { opacity: 0; transform: translateY(12px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes obCardFadeIn {
  0% { opacity: 0; transform: translateY(24px) scale(0.97); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes obBgFadeIn {
  0% { opacity: 0; }
  100% { opacity: 1; }
}
`;

let injected = false;
function injectKeyframes() {
  if (injected) return;
  injected = true;
  const style = document.createElement('style');
  style.textContent = fadeKeyframes;
  document.head.appendChild(style);
}

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [phase, setPhase] = useState<'entering' | 'active' | 'exiting'>('entering');
  const [step, setStep] = useState(0);
  const [modelCount, setModelCount] = useState(0);

  useEffect(() => {
    injectKeyframes();
    const t = setTimeout(() => setPhase('active'), FADE_IN_DURATION + 200);
    return () => clearTimeout(t);
  }, []);

  const setSetupRequired = useAuthStore((s) => s.setSetupRequired);

  const handleEnterPlatform = () => {
    setSetupRequired(false);
    setPhase('exiting');
    setTimeout(() => navigate('/'), FADE_OUT_DURATION);
  };

  const isLeaving = phase === 'exiting';

  const steps = [
    { title: '大模型配置', subtitle: '必配' },
    { title: 'LDAP 集成', subtitle: '可选' },
    { title: '消息渠道', subtitle: '可选' },
  ];

  return (
    <TechBackground
      opacity={phase === 'entering' ? 0 : isLeaving ? 0 : 1}
      style={{
        animation:
          phase === 'entering'
            ? `obBgFadeIn ${FADE_IN_DURATION}ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards`
            : isLeaving
              ? `obBgFadeIn ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19) reverse forwards`
              : 'none',
      }}
    >
      <Card
        style={{
          width: 620,
          borderRadius: 16,
          boxShadow: '0 4px 24px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)',
          position: 'relative',
          ...(phase === 'entering'
            ? {
                opacity: 0,
                animation: `obCardFadeIn ${FADE_IN_DURATION}ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards`,
                animationDelay: '200ms',
              }
            : isLeaving
              ? {
                  opacity: 0,
                  transition: `opacity ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19), transform ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19)`,
                  transform: 'translateY(-12px) scale(0.97)',
                }
              : {}),
        }}
        styles={{ body: { padding: '32px 36px' } }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 12,
            background: token.colorPrimary,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: 20,
            fontWeight: 700,
            margin: '0 auto 12px',
          }}
        >
          A
        </div>

        <Typography.Title
          level={4}
          style={{ margin: '0 0 4px', textAlign: 'center', fontWeight: 600 }}
        >
          欢迎使用 AIOpsOS
        </Typography.Title>
        <Typography.Text
          type="secondary"
          style={{ display: 'block', textAlign: 'center', fontSize: 13, marginBottom: 24 }}
        >
          首次使用需要完成基础配置，仅需 2 分钟
        </Typography.Text>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: 28,
          }}
        >
          {steps.map((s, i) => (
            <div key={s.title} style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{ textAlign: 'center' }}>
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 700,
                    fontSize: 13,
                    transition: 'all 0.3s',
                    background:
                      i < step ? token.colorSuccess : i === step ? token.colorPrimary : '#e8e8e8',
                    color: i <= step ? '#fff' : '#999',
                  }}
                >
                  {i < step ? '✓' : i + 1}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    marginTop: 4,
                    color: i === step ? token.colorText : token.colorTextTertiary,
                    fontWeight: i === step ? 600 : 400,
                  }}
                >
                  {s.title}
                </div>
              </div>
              {i < 2 && (
                <div
                  style={{
                    width: 48,
                    height: 3,
                    borderRadius: 2,
                    margin: '0 8px 20px',
                    background: i < step ? token.colorSuccess : '#e8e8e8',
                    transition: 'background 0.3s',
                  }}
                />
              )}
            </div>
          ))}
        </div>

        <div style={{ minHeight: 280 }}>
          {step === 0 && <Step1ModelConfig onModelCountChange={setModelCount} />}
          {step === 1 && <Step2Ldap />}
          {step === 2 && <Step3Channels />}
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 24,
            paddingTop: 20,
            borderTop: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
            {step === 0 && (modelCount === 0 ? '请至少添加一个大模型' : '')}
            {step > 0 && '此步骤为可选配置'}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {modelCount > 0 && (
              <Typography.Link onClick={handleEnterPlatform} style={{ fontSize: 13 }}>
                进入平台
              </Typography.Link>
            )}
            {step > 0 && (
              <Typography.Link onClick={() => setStep(step - 1)} style={{ fontSize: 13 }}>
                上一步
              </Typography.Link>
            )}
            {step < 2 ? (
              <span
                onClick={() => {
                  if (step === 0 && modelCount === 0) return;
                  setStep(step + 1);
                }}
                style={{
                  display: 'inline-block',
                  padding: '6px 20px',
                  borderRadius: 8,
                  background: step === 0 && modelCount === 0 ? '#d9d9d9' : token.colorPrimary,
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: step === 0 && modelCount === 0 ? 'not-allowed' : 'pointer',
                  transition: 'all 0.3s',
                }}
              >
                下一步
              </span>
            ) : (
              <span
                onClick={handleEnterPlatform}
                style={{
                  display: 'inline-block',
                  padding: '6px 20px',
                  borderRadius: 8,
                  background: token.colorPrimary,
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: 'pointer',
                }}
              >
                进入平台
              </span>
            )}
          </div>
        </div>
      </Card>
    </TechBackground>
  );
}
