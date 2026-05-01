import { useEffect, type ReactNode } from 'react';

const FADE_DURATION = 500;
const KEYFRAMES = `
@keyframes pageFadeIn {
  0% { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
`;

let injected = false;
function injectKeyframes() {
  if (injected) return;
  injected = true;
  const style = document.createElement('style');
  style.textContent = KEYFRAMES;
  document.head.appendChild(style);
}

export default function PageFadeIn({ children }: { children: ReactNode }) {
  useEffect(() => {
    injectKeyframes();
  }, []);

  return (
    <div
      style={{
        animation: `pageFadeIn ${FADE_DURATION}ms ease-out`,
        height: '100%',
      }}
    >
      {children}
    </div>
  );
}
