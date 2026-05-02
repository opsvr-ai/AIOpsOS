import React from 'react';
import { Card, Statistic } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UIStatCard = React.memo(function A2UIStatCard({ node, surfaceId }: A2UIComponentProps) {
  const title = (node.properties.title as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const suffix = (node.properties.suffix as string) || '';
  const { getValue } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? (getValue(valuePath.path) as string | number) ?? '-' : '-';

  return React.createElement(Card, { size: 'small' },
    React.createElement(Statistic, { title, value: resolvedValue, suffix })
  );
});
