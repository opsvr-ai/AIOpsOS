import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { Typography, Layout, Input, Skeleton, theme } from 'antd';
import {
  BookOutlined,
  FileTextOutlined,
  SearchOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  CompassOutlined,
  ApiOutlined,
  ReadOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useDocsStore, type DocCategory } from '@/stores/docsStore';
import MarkdownContent from '@/features/chat/MarkdownContent';
import { useThemeStore } from '@/stores/themeStore';

const { Sider, Content } = Layout;
const { Title, Text } = Typography;

const CATEGORY_META: Record<string, { icon: React.ReactNode; color: string }> = {
  _root: { icon: <CompassOutlined />, color: '#3B82F6' },
  'user-guide': { icon: <ReadOutlined />, color: '#22C55E' },
  'admin-guide': { icon: <SettingOutlined />, color: '#F59E0B' },
  api: { icon: <ApiOutlined />, color: '#8B5CF6' },
};

export default function DocsPage() {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isDark = mode === 'dark';
  const { categories, loading, activeFile, content, contentLoading, fetchDocs, fetchContent } =
    useDocsStore();
  const [collapsed, setCollapsed] = useState(false);
  const [search, setSearch] = useState('');
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  useEffect(() => {
    if (categories.length > 0 && !activeFile) {
      const firstCat = categories[0];
      if (firstCat.files.length > 0) {
        fetchContent(firstCat.files[0].path);
      }
    }
  }, [categories, activeFile, fetchContent]);

  // Scroll content to top when switching docs
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = 0;
    }
  }, [activeFile]);

  const filteredCategories = useMemo(() => {
    if (!search.trim()) return categories;
    const q = search.toLowerCase();
    return categories
      .map((cat) => ({
        ...cat,
        files: cat.files.filter(
          (f) => f.title.toLowerCase().includes(q) || f.name.toLowerCase().includes(q),
        ),
      }))
      .filter((cat) => cat.files.length > 0);
  }, [categories, search]);

  const handleSelectFile = useCallback(
    (path: string) => {
      fetchContent(path);
    },
    [fetchContent],
  );

  const currentCategory = useMemo(() => {
    if (!activeFile) return null;
    for (const cat of categories) {
      if (cat.files.some((f) => f.path === activeFile)) return cat;
    }
    return null;
  }, [activeFile, categories]);

  const currentFile = useMemo(() => {
    if (!activeFile) return null;
    for (const cat of categories) {
      const found = cat.files.find((f) => f.path === activeFile);
      if (found) return found;
    }
    return null;
  }, [activeFile, categories]);

  // Sidebar width
  const sidebarWidth = 280;

  return (
    <Layout
      style={{
        height: '100%',
        background: token.colorBgContainer,
        borderRadius: token.borderRadiusLG,
        overflow: 'hidden',
        border: `1px solid ${token.colorBorder}`,
      }}
      hasSider
    >
      {/* ── Sidebar ────────────────────────────────────────── */}
      <Sider
        width={sidebarWidth}
        collapsedWidth={0}
        collapsible
        collapsed={collapsed}
        trigger={null}
        style={{
          background: isDark ? 'rgba(255,255,255,0.01)' : token.colorBgLayout,
          borderRight: `1px solid ${token.colorBorder}`,
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '20px 20px 14px',
            borderBottom: `1px solid ${token.colorBorder}`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: token.colorPrimary,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 16,
              }}
            >
              <BookOutlined />
            </div>
            <div>
              <Title level={5} style={{ margin: 0, fontSize: 15, lineHeight: 1.2 }}>
                文档中心
              </Title>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {categories.reduce((sum, c) => sum + c.files.length, 0)} 篇文档
              </Text>
            </div>
          </div>
          <Input
            prefix={<SearchOutlined style={{ color: token.colorTextQuaternary }} />}
            placeholder="搜索文档..."
            size="small"
            allowClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              borderRadius: token.borderRadius,
              background: isDark ? 'rgba(255,255,255,0.04)' : token.colorBgContainer,
            }}
          />
        </div>

        {/* Doc Tree */}
        <div style={{ overflow: 'auto', flex: 1, padding: '4px 0' }}>
          {loading ? (
            <div style={{ padding: '12px 16px' }}>
              {[1, 2, 3].map((i) => (
                <div key={i} style={{ marginBottom: 16 }}>
                  <Skeleton.Input active size="small" style={{ width: 90, marginBottom: 8 }} />
                  <Skeleton active paragraph={{ rows: 2 }} title={false} />
                </div>
              ))}
            </div>
          ) : filteredCategories.length === 0 ? (
            <div
              style={{
                padding: '32px 20px',
                textAlign: 'center',
                color: token.colorTextQuaternary,
              }}
            >
              <SearchOutlined style={{ fontSize: 28, marginBottom: 8 }} />
              <br />
              <Text type="secondary" style={{ fontSize: 13 }}>
                未找到匹配的文档
              </Text>
            </div>
          ) : (
            filteredCategories.map((cat) => (
              <SidebarCategory
                key={cat.key}
                category={cat}
                activeFile={activeFile}
                onSelect={handleSelectFile}
                token={token}
                isDark={isDark}
              />
            ))
          )}
        </div>
      </Sider>

      {/* ── Content ────────────────────────────────────────── */}
      <Content
        ref={contentRef}
        style={{
          overflow: 'auto',
          background: token.colorBgContainer,
        }}
      >
        {/* Top bar */}
        <div
          style={{
            padding: '12px 24px',
            borderBottom: `1px solid ${token.colorBorder}`,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            position: 'sticky',
            top: 0,
            zIndex: 10,
            background: token.colorBgContainer,
            backdropFilter: 'blur(8px)',
          }}
        >
          <span
            onClick={() => setCollapsed(!collapsed)}
            style={{
              cursor: 'pointer',
              color: token.colorTextTertiary,
              fontSize: 16,
              display: 'flex',
              alignItems: 'center',
              padding: '2px 4px',
              borderRadius: 4,
              transition: 'color 0.15s',
            }}
          >
            {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </span>

          {currentCategory && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {currentCategory.label}
            </Text>
          )}
          {currentCategory && currentFile && (
            <>
              <Text type="secondary" style={{ fontSize: 12, opacity: 0.4 }}>
                /
              </Text>
              <Text style={{ fontSize: 12, fontWeight: 500, color: token.colorText }}>
                {currentFile.title}
              </Text>
            </>
          )}
        </div>

        {/* Document body */}
        <div style={{ padding: '32px 40px', maxWidth: 820, margin: '0 auto' }}>
          {contentLoading ? (
            <div>
              <Skeleton.Input active size="large" style={{ width: 280, marginBottom: 20 }} />
              <Skeleton active paragraph={{ rows: 3 }} style={{ marginBottom: 20 }} />
              <Skeleton active paragraph={{ rows: 5 }} title={false} style={{ marginBottom: 20 }} />
              <Skeleton active paragraph={{ rows: 4 }} title={false} />
            </div>
          ) : content ? (
            <MarkdownContent>{content}</MarkdownContent>
          ) : (
            <div
              style={{
                textAlign: 'center',
                paddingTop: 80,
                color: token.colorTextQuaternary,
              }}
            >
              <FileTextOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.4 }} />
              <br />
              <Text type="secondary">请从左侧选择文档</Text>
            </div>
          )}
        </div>
      </Content>
    </Layout>
  );
}

/** A category group in the sidebar */
function SidebarCategory({
  category,
  activeFile,
  onSelect,
  token,
  isDark,
}: {
  category: DocCategory;
  activeFile: string | null;
  onSelect: (path: string) => void;
  token: ReturnType<typeof theme.useToken>['token'];
  isDark: boolean;
}) {
  const meta = CATEGORY_META[category.key] ?? {
    icon: <BookOutlined />,
    color: token.colorPrimary,
  };

  return (
    <div style={{ marginBottom: 2 }}>
      {/* Category header */}
      <div
        style={{
          padding: '10px 20px 6px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span
          style={{
            fontSize: 12,
            color: meta.color,
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {meta.icon}
        </span>
        <Text
          type="secondary"
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
          }}
        >
          {category.label}
        </Text>
      </div>

      {/* Files */}
      {category.files.map((f) => {
        const isActive = activeFile === f.path;
        return (
          <div
            key={f.path}
            onClick={() => onSelect(f.path)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '7px 20px 7px 32px',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
              borderRight: isActive ? `3px solid ${meta.color}` : '3px solid transparent',
              background: isActive
                ? isDark
                  ? 'rgba(255,255,255,0.06)'
                  : `${meta.color}0A`
                : 'transparent',
              marginRight: 0,
            }}
            onMouseEnter={(e) => {
              if (!isActive)
                (e.currentTarget as HTMLElement).style.background = isDark
                  ? 'rgba(255,255,255,0.03)'
                  : 'rgba(0,0,0,0.02)';
            }}
            onMouseLeave={(e) => {
              if (!isActive) (e.currentTarget as HTMLElement).style.background = 'transparent';
            }}
          >
            <span
              style={{
                width: 5,
                height: 5,
                borderRadius: '50%',
                background: isActive ? meta.color : token.colorBorder,
                flexShrink: 0,
                transition: 'all 0.2s ease',
              }}
            />
            <span
              style={{
                fontSize: 13,
                fontWeight: isActive ? 500 : 400,
                color: isActive ? token.colorText : token.colorTextSecondary,
                lineHeight: 1.4,
                transition: 'color 0.15s',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {f.title}
            </span>
          </div>
        );
      })}
    </div>
  );
}
