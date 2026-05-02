import React, { useState } from 'react';
import { Tabs as AntTabs } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';

export const A2UITabs = React.memo(function A2UITabs({ node, surfaceId }: A2UIComponentProps) {
  const titles = (node.properties.tabTitles as string[]) || [];
  const [activeKey, setActiveKey] = useState('0');

  const items = node.children.map((child, i) => ({
    key: String(i),
    label: titles[i] || `Tab ${i + 1}`,
    children: React.createElement(RenderA2UINode, { node: child, surfaceId }),
  }));

  return React.createElement(AntTabs, {
    activeKey,
    onChange: setActiveKey,
    items,
    size: 'small',
  });
});
