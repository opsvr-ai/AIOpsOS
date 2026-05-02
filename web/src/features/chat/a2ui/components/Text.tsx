import React from 'react';
import type { A2UIComponentProps } from '../registry';

const SIZE_MAP: Record<string, React.CSSProperties> = {
  h1: { fontSize: 22, fontWeight: 700, margin: '0 0 8px', lineHeight: 1.4 },
  h2: { fontSize: 18, fontWeight: 600, margin: '0 0 6px', lineHeight: 1.35 },
  h3: { fontSize: 16, fontWeight: 600, margin: '0 0 4px', lineHeight: 1.3 },
  h4: { fontSize: 15, fontWeight: 500, margin: '0 0 4px', lineHeight: 1.3 },
  h5: { fontSize: 14, fontWeight: 500, margin: '0 0 2px', lineHeight: 1.25 },
  caption: { fontSize: 12, fontWeight: 400, margin: '0', opacity: 0.65 },
  body: { fontSize: 14, fontWeight: 400, margin: '0', lineHeight: 1.5 },
};

export const A2UIText = React.memo(function A2UIText({ node }: A2UIComponentProps) {
  const text = (node.properties.text as string) ?? '';
  const variant = (node.properties.variant as string) || 'body';
  const style = SIZE_MAP[variant] || SIZE_MAP.body;
  const usageHint = node.properties.usageHint as string | undefined;

  return React.createElement(usageHint === 'caption' ? 'span' : 'div', { style }, text);
});
