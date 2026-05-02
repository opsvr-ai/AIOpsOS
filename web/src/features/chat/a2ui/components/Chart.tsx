import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

const COLORS = ['#1677ff', '#52c41a', '#fa8c16', '#eb2f96', '#722ed1', '#13c2c2'];

export const A2UIChart = React.memo(function A2UIChart({ node, surfaceId }: A2UIComponentProps) {
  const chartType = (node.properties.chartType as string) || 'bar';
  const dataPath = node.properties.data as PathValue | undefined;
  const xKey = (node.properties.xKey as string) || 'name';
  const yKey = (node.properties.yKey as string) || 'value';
  const height = (node.properties.height as number) || 250;
  const { getValue } = useA2UISurface(surfaceId);

  const data = useMemo(() => {
    if (!dataPath) return [];
    if (Array.isArray(dataPath)) return dataPath as unknown as Record<string, unknown>[];
    const raw = getValue(dataPath.path);
    return Array.isArray(raw) ? (raw as Record<string, unknown>[]) : [];
  }, [dataPath, getValue]);

  if (data.length === 0) {
    return React.createElement(
      'div',
      { style: { padding: 24, textAlign: 'center', color: '#999' } },
      'No data',
    );
  }

  let chart: React.ReactElement;
  switch (chartType) {
    case 'pie':
      chart = React.createElement(
        PieChart,
        {},
        React.createElement(
          Pie,
          {
            data,
            dataKey: yKey,
            nameKey: xKey,
            cx: '50%',
            cy: '50%',
            outerRadius: 80,
            label: true,
          },
          data.map((_: unknown, i: number) =>
            React.createElement(Cell, { key: i, fill: COLORS[i % COLORS.length] }),
          ),
        ),
        React.createElement(Tooltip),
      );
      break;
    case 'line':
      chart = React.createElement(
        LineChart,
        { data },
        React.createElement(XAxis, { dataKey: xKey }),
        React.createElement(YAxis),
        React.createElement(Tooltip),
        React.createElement(Line, { type: 'monotone', dataKey: yKey, stroke: '#1677ff' }),
      );
      break;
    case 'bar':
    default:
      chart = React.createElement(
        BarChart,
        { data },
        React.createElement(XAxis, { dataKey: xKey }),
        React.createElement(YAxis),
        React.createElement(Tooltip),
        React.createElement(Bar, { dataKey: yKey, fill: '#1677ff', radius: [4, 4, 0, 0] }),
      );
      break;
  }

  return React.createElement(ResponsiveContainer, { width: '100%', height, children: chart });
});
