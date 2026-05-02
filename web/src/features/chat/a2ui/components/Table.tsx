import React, { useMemo } from 'react';
import { Table as AntTable } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

interface ColumnDef {
  key: string;
  title: string;
  sortable?: boolean;
  width?: number;
}

export const A2UITable = React.memo(function A2UITable({ node, surfaceId }: A2UIComponentProps) {
  const columns = (node.properties.columns as ColumnDef[]) || [];
  const dataPath = node.properties.data as PathValue | undefined;
  const pagination = (node.properties.pagination as boolean) ?? true;
  const { getValue } = useA2UISurface(surfaceId);

  const dataSource = useMemo(() => {
    if (!dataPath) return [];
    // Support both path binding {path: "/data/rows"} and literal arrays
    if (Array.isArray(dataPath)) return dataPath as unknown as Record<string, unknown>[];
    const raw = getValue(dataPath.path);
    return Array.isArray(raw) ? raw : [];
  }, [dataPath, getValue]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const antColumns: any[] = columns.map((col) => ({
    key: col.key,
    title: col.title,
    dataIndex: col.key,
    sorter: col.sortable
      ? (a: any, b: any) => {
          const av = a[col.key];
          const bv = b[col.key];
          if (typeof av === 'string') return av.localeCompare(String(bv));
          if (typeof av === 'number') return av - bv;
          return 0;
        }
      : undefined,
    width: col.width,
    render: (val: unknown) => {
      if (val === null || val === undefined) return '-';
      if (typeof val === 'boolean') return val ? 'Yes' : 'No';
      if (typeof val === 'object') return JSON.stringify(val);
      return String(val);
    },
  }));

  return React.createElement(AntTable as any, {
    columns: antColumns,
    dataSource: dataSource.map((row: any, i: number) => ({ ...row, _key: i })),
    rowKey: '_key',
    size: 'small',
    pagination: pagination ? { size: 'small', defaultPageSize: 10 } : false,
  });
});
