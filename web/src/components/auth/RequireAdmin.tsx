import { Navigate, Outlet } from 'react-router-dom';
import { Result, Button } from 'antd';
import { useAuthStore } from '@/stores/authStore';

export default function RequireAdmin() {
  const user = useAuthStore((s) => s.user);
  const hasAdmin = user?.roles?.includes('admin');

  if (!user) return <Navigate to="/login" replace />;
  if (!hasAdmin) {
    return (
      <Result
        status="403"
        title="无访问权限"
        subTitle="仅平台管理员可访问控制中心"
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回
          </Button>
        }
      />
    );
  }
  return <Outlet />;
}
