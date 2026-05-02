import React from 'react';
import { Divider as AntDivider } from 'antd';

export const A2UIDivider = React.memo(function A2UIDivider() {
  return React.createElement(AntDivider, { style: { margin: '12px 0' } });
});
