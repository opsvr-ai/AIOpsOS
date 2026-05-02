import React, { useState, useEffect } from 'react';
import { Checkbox, Radio } from 'antd';
import type { RadioChangeEvent } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UIMultipleChoice = React.memo(function A2UIMultipleChoice({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const options = (node.properties.options as { value: string; label: string }[]) || [];
  const variant = (node.properties.variant as string) || 'checkbox';
  const { getValue, dispatch } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? (getValue(valuePath.path) as string | string[]) ?? [] : [];
  const isMulti = variant === 'checkbox';
  const defaultValue = isMulti
    ? (Array.isArray(resolvedValue) ? resolvedValue : [])
    : resolvedValue;
  const [localValue, setLocalValue] = useState<string | string[]>(defaultValue);

  useEffect(() => {
    setLocalValue(isMulti
      ? (Array.isArray(resolvedValue) ? resolvedValue : [resolvedValue as string])
      : resolvedValue);
  }, [resolvedValue, isMulti]);

  const handleMultiChange = (val: unknown) => {
    setLocalValue(val as string[]);
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: val });
    }
  };

  const handleSingleChange = (e: RadioChangeEvent) => {
    setLocalValue(e.target.value);
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: e.target.value });
    }
  };

  const opts = options.map((o) => ({ value: o.value, label: o.label }));

  return React.createElement('div', { style: { marginBottom: 12 } },
    label && React.createElement('label', {
      style: { display: 'block', marginBottom: 4, fontSize: 13, fontWeight: 500 },
    }, label),
    isMulti
      ? React.createElement(Checkbox.Group, {
          options: opts,
          value: localValue as string[],
          onChange: handleMultiChange,
          style: { display: 'flex', gap: 12, flexWrap: 'wrap' },
        })
      : React.createElement(Radio.Group, {
          options: opts,
          value: localValue as string,
          onChange: handleSingleChange,
        })
  );
});
