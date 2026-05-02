import React from 'react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UIDateTimeInput = React.memo(function A2UIDateTimeInput({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const enableDate = node.properties.enableDate !== false;
  const enableTime = node.properties.enableTime === true;
  const { getValue, dispatch } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? String(getValue(valuePath.path) ?? '') : '';

  const handleChange = (_: unknown, dateStr: string | string[]) => {
    const val = Array.isArray(dateStr) ? dateStr[0] : dateStr;
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: val });
    }
  };

  const pickerValue = resolvedValue ? dayjs(resolvedValue) : undefined;

  return React.createElement('div', { style: { marginBottom: 12 } },
    label && React.createElement('label', {
      style: { display: 'block', marginBottom: 4, fontSize: 13, fontWeight: 500 },
    }, label),
    React.createElement(DatePicker, {
      value: pickerValue,
      showTime: enableTime,
      picker: enableDate ? 'date' : 'time',
      onChange: handleChange,
      style: { borderRadius: 6, width: '100%' },
    })
  );
});
