import React from 'react';
import { Modal } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';

export const A2UIConfirmDialog = React.memo(function A2UIConfirmDialog({ node, surfaceId }: A2UIComponentProps) {
  const title = (node.properties.title as string) || 'Confirm';
  const message = (node.properties.message as string) || '';
  const okText = (node.properties.okText as string) || 'OK';
  const cancelText = (node.properties.cancelText as string) || 'Cancel';
  const danger = node.properties.danger as boolean | undefined;
  const { dispatch } = useA2UISurface(surfaceId);

  return React.createElement(Modal, {
    title,
    open: true,
    onOk: () => dispatch('confirm', {}),
    onCancel: () => dispatch('cancel', {}),
    okText,
    cancelText,
    okButtonProps: { danger },
  }, React.createElement('p', {}, message));
});
