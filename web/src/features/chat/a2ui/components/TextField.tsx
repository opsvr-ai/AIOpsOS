import React, { useState, useEffect } from 'react';
import { Input } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UITextField = React.memo(function A2UITextField({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const placeholder = (node.properties.placeholder as string) || '';
  const obscured = node.properties.textFieldType === 'obscured';
  const { getValue, dispatch } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? String(getValue(valuePath.path) ?? '') : '';
  const [localValue, setLocalValue] = useState(resolvedValue);

  useEffect(() => {
    setLocalValue(resolvedValue);
  }, [resolvedValue]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalValue(e.target.value);
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: e.target.value });
    }
  };

  return React.createElement('div', { style: { marginBottom: 12 } },
    label && React.createElement('label', {
      style: { display: 'block', marginBottom: 4, fontSize: 13, fontWeight: 500 },
    }, label),
    obscured
      ? React.createElement(Input.Password, {
          value: localValue,
          onChange: handleChange,
          placeholder,
          style: { borderRadius: 6 },
        })
      : React.createElement(Input, {
          value: localValue,
          onChange: handleChange,
          placeholder,
          style: { borderRadius: 6 },
        })
  );
});
