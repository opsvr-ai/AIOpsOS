import React, { useState, useEffect } from 'react';
import { Checkbox } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UICheckBox = React.memo(function A2UICheckBox({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const { getValue, dispatch } = useA2UISurface(surfaceId);

  const resolvedValue = valuePath ? !!getValue(valuePath.path) : false;
  const [checked, setChecked] = useState(resolvedValue);

  useEffect(() => {
    setChecked(resolvedValue);
  }, [resolvedValue]);

  const handleChange = (e: { target: { checked: boolean } }) => {
    setChecked(e.target.checked);
    if (valuePath) {
      dispatch('change', { path: valuePath.path, value: e.target.checked });
    }
  };

  return React.createElement(Checkbox, {
    checked,
    onChange: handleChange,
    style: { marginBottom: 8 },
  }, label);
});
