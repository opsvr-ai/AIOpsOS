import type { ReactNode } from 'react';

interface Props {
  children?: ReactNode;
  opacity?: number;
  style?: React.CSSProperties;
}

export default function TechBackground({ children, opacity = 1, style }: Props) {
  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #EEF2FF 0%, #F9FAFB 50%, #ECFDF5 100%)',
        position: 'relative',
        overflow: 'hidden',
        opacity,
        ...style,
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(circle, rgba(37,99,235,0.06) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }}
      />
      {children}
    </div>
  );
}
