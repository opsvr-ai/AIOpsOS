import React from 'react';
import { Input, Button, message as antMsg } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UICodeEditor = React.memo(function A2UICodeEditor({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const contentPath = node.properties.content as PathValue | string | undefined;
  const language = (node.properties.language as string) || '';
  const readOnly = node.properties.readOnly !== false;
  const { getValue } = useA2UISurface(surfaceId);

  const content = typeof contentPath === 'object' && 'path' in contentPath
    ? String(getValue(contentPath.path) ?? '')
    : typeof contentPath === 'string'
      ? contentPath
      : '';

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => {
      antMsg.success('Copied');
    }).catch(() => {
      antMsg.error('Failed to copy');
    });
  };

  return React.createElement('div', { style: { marginBottom: 12 } },
    (label || language) && React.createElement('div', {
      style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
    },
      React.createElement('span', { style: { fontSize: 12, fontWeight: 500, color: '#666' } },
        language ? `${label} (${language})` : label
      ),
      React.createElement(Button, {
        size: 'small', icon: React.createElement(CopyOutlined), onClick: handleCopy, type: 'text',
      })
    ),
    React.createElement(Input.TextArea, {
      value: content,
      readOnly,
      rows: (node.properties.rows as number) || 8,
      style: { fontFamily: 'monospace', fontSize: 13, borderRadius: 6 },
      autoSize: !readOnly ? { minRows: 4, maxRows: 20 } : undefined,
    })
  );
});
