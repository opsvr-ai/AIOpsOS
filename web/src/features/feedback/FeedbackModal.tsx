import { Modal } from 'antd';
import { BugOutlined } from '@ant-design/icons';
import FeedbackPage from './FeedbackPage';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function FeedbackModal({ open, onClose }: Props) {
  return (
    <Modal
      title={
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <BugOutlined style={{ color: '#1677ff' }} />
          <span>需求 & Bug 反馈</span>
        </span>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={960}
      destroyOnHidden
      style={{ top: 40 }}
      styles={{
        body: { maxHeight: 'calc(100vh - 200px)', overflow: 'auto', padding: '16px 24px' },
      }}
    >
      <FeedbackPage />
    </Modal>
  );
}
