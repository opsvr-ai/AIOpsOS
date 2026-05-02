import React, { useState, useEffect } from 'react';
import { Slider as AntSlider } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UISlider = React.memo(function A2UISlider({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const min = (node.properties.minValue as number) ?? 0;
  const max = (node.properties.maxValue as number) ?? 100;
  const { getValue, dispatch } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? Number(getValue(valuePath.path) ?? min) : min;
  const [localValue, setLocalValue] = useState(resolvedValue);

  useEffect(() => {
    setLocalValue(resolvedValue);
  }, [resolvedValue]);

  const handleChange = (val: number) => {
    setLocalValue(val);
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: val });
    }
  };

  return React.createElement('div', { style: { marginBottom: 12 } },
    label && React.createElement('label', {
      style: { display: 'block', marginBottom: 4, fontSize: 13, fontWeight: 500 },
    }, `${label}: ${localValue}`),
    React.createElement(AntSlider, {
      value: localValue,
      min,
      max,
      onChange: handleChange,
      style: { marginBottom: 4 },
    })
  );
});
