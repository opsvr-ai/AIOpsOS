import { useEffect, useState } from 'react';
import { Select, theme } from 'antd';
import { PlusOutlined, TeamOutlined } from '@ant-design/icons';
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

  useEffect(() => {
    fetchSpaces();
  }, [spaceVersion]);

  const fetchSpaces = async () => {
    setLoading(true);
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
      /* ignore */
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
      options={[
        ...spaces.map((s) => ({
          value: s.id,
          label: (
            <span>
              <TeamOutlined style={{ marginRight: 6, color: token.colorTextTertiary }} />
              {s.name}
            </span>
          ),
        })),
        {
          value: '__manage__',
          label: (
            <span style={{ color: token.colorPrimary }}>
              <PlusOutlined style={{ marginRight: 6 }} />
              管理空间
            </span>
          ),
        },
      ]}
    />
  );
}
