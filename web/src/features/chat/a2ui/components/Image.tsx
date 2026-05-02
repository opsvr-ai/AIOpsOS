import React from 'react';
import type { A2UIComponentProps } from '../registry';

export const A2UIImage = React.memo(function A2UIImage({ node }: A2UIComponentProps) {
  const url = (node.properties.url as string) ?? '';
  const alt = (node.properties.altText as string) || '';
  const fit = (node.properties.fit as string) || 'cover';
  return React.createElement('img', {
    src: url,
    alt,
    style: { maxWidth: '100%', objectFit: fit as React.CSSProperties['objectFit'], borderRadius: 8 },
  });
});
