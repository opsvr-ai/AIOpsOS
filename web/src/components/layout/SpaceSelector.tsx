import { useEffect, useState } from 'react';
import { Button, Select, Space, theme, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined, TeamOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useSpaceStore } from '@/stores/spaceStore';
import { useAuthStore } from '@/stores/authStore';
import api from '@/services/api';

interface SpaceItem {
  id: string;
  name: string;
  my_role: string | null;
  member_count: number;
}

export default function SpaceSelector() {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const currentSpace = useSpaceStore((s) => s.currentSpace);
  const setCurrentSpace = useSpaceStore((s) => s.setCurrentSpace);
  const spaceVersion = useSpaceStore((s) => s.spaceVersion);
  const defaultSpaceId = useAuthStore((s) => s.user?.default_space_id);
  const [spaces, setSpaces] = useState<SpaceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchSpaces();
  }, [spaceVersion]);

  const fetchSpaces = async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await api.get('/spaces');
      const data: SpaceItem[] = res.data ?? [];
      setSpaces(data);
      if (!currentSpace && data.length > 0) {
        const preferred = data.find((s: SpaceItem) => s.id === defaultSpaceId) || data[0];
        setCurrentSpace({
          id: preferred.id,
          name: preferred.name,
          my_role: preferred.my_role ?? 'admin',
        });
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (spaceId: string) => {
    if (spaceId === '__manage__') {
      navigate('/spaces');
      return;
    }
    const space = spaces.find((s) => s.id === spaceId);
    if (space) {
      setCurrentSpace({ id: space.id, name: space.name, my_role: space.my_role ?? 'member' });
    }
  };

  const seenIds = new Set(spaces.map((s) => s.id));
  const optionItems = [
    ...(currentSpace && !seenIds.has(currentSpace.id)
      ? [{ value: currentSpace.id, label: currentSpace.name }]
      : []),
    ...spaces.map((s) => ({ value: s.id, label: s.name })),
    { value: '__manage__', label: '管理空间' },
  ];

  if (error) {
    return (
      <Space size={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          加载失败
        </Typography.Text>
        <Button
          type="link"
          size="small"
          icon={<ReloadOutlined />}
          onClick={fetchSpaces}
          style={{ padding: 0, fontSize: 12 }}
        >
          重试
        </Button>
      </Space>
    );
  }

  return (
    <Select
      value={currentSpace?.id ?? undefined}
      onChange={handleChange}
      loading={loading}
      placeholder="选择空间"
      style={{ minWidth: 140, maxWidth: 200 }}
      size="small"
      variant="borderless"
      dropdownStyle={{ minWidth: 200 }}
      options={optionItems}
      optionRender={(option) => {
        if (option.value === '__manage__') {
          return (
            <span style={{ color: token.colorPrimary }}>
              <PlusOutlined style={{ marginRight: 6 }} />
              管理空间
            </span>
          );
        }
        return (
          <span>
            <TeamOutlined style={{ marginRight: 6, color: token.colorTextTertiary }} />
            {option.label}
          </span>
        );
      }}
    />
  );
}
