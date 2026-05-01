import { useEffect, useState } from 'react';
import { Card, Form, Input, Button, App, Typography } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { authApi, ProfileData, PasswordChangeData } from '@/services/auth';
import { useAuthStore } from '@/stores/authStore';

export default function ProfilePage() {
  const { message } = App.useApp();
  const user = useAuthStore((s) => s.user);
  const setAuth = useAuthStore((s) => s.setAuth);
  const tokenStr = useAuthStore((s) => s.token);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const [profileForm] = Form.useForm();
  const [pwdForm] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [changingPwd, setChangingPwd] = useState(false);

  useEffect(() => {
    if (user) {
      profileForm.setFieldsValue(user);
    }
  }, [user, profileForm]);

  const handleSaveProfile = async (values: ProfileData) => {
    setSaving(true);
    try {
      await authApi.updateProfile(values);
      if (tokenStr && refreshToken) {
        setAuth(tokenStr, refreshToken, {
          ...user!,
          ...values,
        });
      }
      message.success('个人信息已更新');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async (values: PasswordChangeData) => {
    setChangingPwd(true);
    try {
      await authApi.changePassword(values);
      message.success('密码已修改');
      pwdForm.resetFields();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '修改失败');
    } finally {
      setChangingPwd(false);
    }
  };

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 20, fontWeight: 600 }}>
        <UserOutlined style={{ marginRight: 8 }} />
        个人信息
      </Typography.Title>

      <Card title="基本信息" style={{ marginBottom: 20, borderRadius: 12 }}>
        <Form
          form={profileForm}
          layout="vertical"
          onFinish={handleSaveProfile}
          style={{ maxWidth: 480 }}
        >
          <Form.Item name="username" label="用户名">
            <Input disabled />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="display_name" label="姓名">
            <Input placeholder="输入姓名" />
          </Form.Item>
          <Form.Item name="phone" label="手机号">
            <Input placeholder="输入手机号" />
          </Form.Item>
          <Form.Item name="department" label="部门">
            <Input placeholder="输入部门" />
          </Form.Item>
          <Form.Item name="title" label="职位">
            <Input placeholder="输入职位" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="修改密码" style={{ borderRadius: 12 }}>
        <Form
          form={pwdForm}
          layout="vertical"
          onFinish={handleChangePassword}
          style={{ maxWidth: 480 }}
        >
          <Form.Item
            name="old_password"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="当前密码" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少6位' },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="新密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={changingPwd}>
              修改密码
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
