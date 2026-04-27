import { useEffect, useState, useCallback } from 'react';
import {
  Input,
  Tabs,
  Skeleton,
  Typography,
  Empty,
  Button,
  theme,
  Segmented,
  Tag,
  Tooltip,
  Checkbox,
  Popconfirm,
} from 'antd';
import {
  SearchOutlined,
  UserOutlined,
  TeamOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  ApartmentOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import { AnimatePresence, motion } from 'framer-motion';
import { useMemoryStore } from '@/stores/memoryStore';
import MemoryCard from './MemoryCard';
import MemoryGraph from './MemoryGraph';
import MemoryDetailPanel from './MemoryDetailPanel';

export default function MemoryPage() {
  const { token } = theme.useToken();
  const {
    memories,
    loading,
    hasMore,
    loadMore,
    fetchMemories,
    fetchGraph,
    fetchTags,
    deleteMemory,
    allTags,
    selectedTag,
    selectedMemoryId,
    viewMode,
    focusTag,
    clearFocus,
    setViewMode,
    scope,
  } = useMemoryStore();
  const [activeTab, setActiveTab] = useState<'personal' | 'team'>(
    scope === 'team' ? 'team' : 'personal',
  );
  const [query, setQuery] = useState('');
  const [tagSidebarCollapsed, setTagSidebarCollapsed] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);

  // Load data on mount and tab change
  useEffect(() => {
    fetchMemories({ scope: activeTab });
    fetchTags(activeTab);
    fetchGraph({ scope: activeTab });
  }, [activeTab]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = useCallback(
    (value: string) => {
      setQuery(value);
      const timer = setTimeout(() => {
        if (viewMode === 'list') {
          fetchMemories({ scope: activeTab, query: value });
        }
      }, 300);
      return () => clearTimeout(timer);
    },
    [activeTab, fetchMemories, viewMode],
  );

  const handleSelectMemory = useCallback((id: string) => {
    useMemoryStore.setState({ selectedMemoryId: id });
  }, []);

  const handleCloseDetail = useCallback(() => {
    useMemoryStore.setState({ selectedMemoryId: '' });
  }, []);

  const handleFocusTag = useCallback(
    (tag: string) => {
      focusTag(tag);
    },
    [focusTag],
  );

  const handleSelect = useCallback((id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const handleTagFilter = useCallback(
    (tag: string) => {
      setQuery(tag);
      fetchMemories({ scope: activeTab, query: tag, tags: tag });
    },
    [activeTab, fetchMemories],
  );

  // Filter memories by scope, then by search query (title/content/tags)
  const searchLower = query.toLowerCase();
  const filtered = memories.filter((m) => {
    if (m.scope !== activeTab) return false;
    if (!searchLower) return true;
    return (
      m.title?.toLowerCase().includes(searchLower) ||
      m.content?.toLowerCase().includes(searchLower) ||
      m.tags?.some((t) => t.toLowerCase().includes(searchLower))
    );
  });

  const handleSelectAll = useCallback(
    (checked: boolean) => {
      if (checked) {
        setSelectedIds(new Set(filtered.map((m) => m.id)));
      } else {
        setSelectedIds(new Set());
      }
    },
    [filtered],
  );

  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;
    setBatchDeleting(true);
    for (const id of selectedIds) {
      try {
        await deleteMemory(id);
      } catch {
        /* continue */
      }
    }
    setSelectedIds(new Set());
    setBatchDeleting(false);
    fetchMemories({ scope: activeTab });
  }, [selectedIds, deleteMemory, fetchMemories, activeTab]);

  const tabItems = [
    {
      key: 'personal',
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <UserOutlined />
          我的记忆
        </span>
      ),
    },
    {
      key: 'team',
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <TeamOutlined />
          组织记忆
        </span>
      ),
    },
  ];

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Top bar */}
      <div
        style={{
          padding: '16px 24px 0',
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 12,
          }}
        >
          <div>
            <Typography.Title level={4} style={{ margin: 0, color: token.colorText }}>
              <ApartmentOutlined style={{ marginRight: 8, color: token.colorPrimary }} />
              记忆管理
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              从对话中提取的个人运维经验和团队公共知识
            </Typography.Text>
          </div>
          <Segmented
            value={viewMode}
            onChange={(val) => {
              setViewMode(val as 'graph' | 'list');
              if (val === 'graph') {
                fetchGraph({ scope: activeTab });
                fetchTags(activeTab);
              }
            }}
            options={[
              { value: 'graph', icon: <AppstoreOutlined />, label: '知识图谱' },
              { value: 'list', icon: <UnorderedListOutlined />, label: '列表' },
            ]}
            style={{ fontSize: 12 }}
          />
        </div>

        {/* Search + scope tabs */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 0 }}>
          <Input
            prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
            placeholder={viewMode === 'graph' ? '搜索节点...' : '搜索记忆...'}
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            allowClear
            style={{ borderRadius: 10, height: 38, fontSize: 13, flex: 1 }}
          />
          <Tabs
            activeKey={activeTab}
            onChange={(key) => {
              setActiveTab(key as 'personal' | 'team');
              useMemoryStore.setState({ selectedMemoryId: '', selectedTag: '' });
            }}
            items={tabItems}
            style={{ marginBottom: 0 }}
          />
        </div>

        {/* Tag filter chips */}
        {allTags.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 6,
              padding: '8px 0 10px',
              overflow: 'auto',
              flexWrap: 'wrap',
            }}
          >
            {selectedTag && (
              <Tag
                closable
                onClose={clearFocus}
                color="#f0a500"
                style={{ cursor: 'pointer', borderRadius: 4, fontSize: 11 }}
              >
                {selectedTag}
              </Tag>
            )}
            {allTags.slice(0, 12).map((t) => (
              <Tooltip key={t.name} title={`${t.count} 条记忆`}>
                <Tag
                  style={{
                    cursor: 'pointer',
                    borderRadius: 4,
                    fontSize: 11,
                    border: `1px solid ${token.colorBorderSecondary}`,
                    background:
                      selectedTag === t.name ? token.colorPrimaryBg : token.colorFillQuaternary,
                    color: selectedTag === t.name ? token.colorPrimary : token.colorTextSecondary,
                    fontWeight: selectedTag === t.name ? 600 : 400,
                  }}
                  onClick={() => handleFocusTag(t.name)}
                >
                  {t.name}
                  <span style={{ marginLeft: 4, opacity: 0.4, fontSize: 10 }}>{t.count}</span>
                </Tag>
              </Tooltip>
            ))}
          </div>
        )}
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>
        <AnimatePresence mode="wait">
          {viewMode === 'list' ? (
            <motion.div
              key="list-view"
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              transition={{ duration: 0.2 }}
              style={{
                flex: 1,
                overflow: 'auto',
                padding: '16px 24px',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              {/* Batch actions bar */}
              {selectedIds.size > 0 && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '10px 16px',
                    marginBottom: 12,
                    borderRadius: 8,
                    background: token.colorPrimaryBg,
                    border: `1px solid ${token.colorPrimaryBorder}`,
                    flexShrink: 0,
                  }}
                >
                  <Checkbox
                    checked={selectedIds.size === filtered.length}
                    indeterminate={selectedIds.size > 0 && selectedIds.size < filtered.length}
                    onChange={(e) => handleSelectAll(e.target.checked)}
                  />
                  <Typography.Text style={{ fontSize: 13, color: token.colorPrimary, flex: 1 }}>
                    已选 {selectedIds.size} 项
                  </Typography.Text>
                  <Button size="small" onClick={() => setSelectedIds(new Set())}>
                    取消
                  </Button>
                  <Popconfirm
                    title={`确认删除 ${selectedIds.size} 条记忆？`}
                    onConfirm={handleBatchDelete}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button size="small" danger loading={batchDeleting}>
                      批量删除
                    </Button>
                  </Popconfirm>
                </div>
              )}

              {loading ? (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                    gap: 12,
                  }}
                >
                  {[1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} active paragraph={{ rows: 3 }} />
                  ))}
                </div>
              ) : filtered.length === 0 ? (
                <Empty
                  description={
                    <span style={{ color: token.colorTextTertiary, fontSize: 13 }}>
                      {query
                        ? `未找到匹配 "${query}" 的记忆`
                        : activeTab === 'personal'
                          ? '暂无个人记忆'
                          : '暂无组织记忆'}
                      。开始对话后，智能体会自动提取运维经验。
                    </span>
                  }
                  style={{ paddingTop: 60 }}
                />
              ) : (
                <>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                      gap: 12,
                      alignItems: 'start',
                    }}
                  >
                    {filtered.map((m) => (
                      <MemoryCard
                        key={m.id}
                        memory={m}
                        onDelete={deleteMemory}
                        selected={selectedIds.has(m.id)}
                        onSelect={handleSelect}
                        onTagClick={handleTagFilter}
                      />
                    ))}
                  </div>
                  {hasMore && (
                    <Button
                      type="dashed"
                      loading={loading}
                      onClick={loadMore}
                      style={{ borderRadius: 8, fontSize: 13, marginTop: 16, alignSelf: 'center' }}
                    >
                      加载更多
                    </Button>
                  )}
                </>
              )}
            </motion.div>
          ) : (
            <motion.div
              key="graph-view"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}
            >
              {/* Tag sidebar */}
              <AnimatePresence initial={false}>
                {!tagSidebarCollapsed && (
                  <motion.div
                    key="tag-sidebar"
                    initial={{ width: 0, opacity: 0 }}
                    animate={{ width: 170, opacity: 1 }}
                    exit={{ width: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    style={{ overflow: 'hidden', flexShrink: 0 }}
                  >
                    <div
                      style={{
                        width: 170,
                        height: '100%',
                        borderRight: `1px solid ${token.colorBorderSecondary}`,
                        background: token.colorBgLayout,
                        overflow: 'auto',
                        padding: '12px 10px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 2,
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          marginBottom: 6,
                          paddingLeft: 4,
                          paddingRight: 4,
                        }}
                      >
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          <TagsOutlined style={{ marginRight: 4 }} />
                          标签分类
                        </Typography.Text>
                        <Button
                          type="text"
                          size="small"
                          icon={<MenuFoldOutlined />}
                          onClick={() => setTagSidebarCollapsed(true)}
                          style={{ color: token.colorTextTertiary, fontSize: 11 }}
                        />
                      </div>
                      {allTags.length === 0 ? (
                        <Typography.Text
                          type="secondary"
                          style={{ fontSize: 11, padding: '8px 4px' }}
                        >
                          暂无标签
                        </Typography.Text>
                      ) : (
                        allTags.map((t) => (
                          <div
                            key={t.name}
                            onClick={() => handleFocusTag(t.name)}
                            style={{
                              padding: '5px 8px',
                              borderRadius: 6,
                              cursor: 'pointer',
                              fontSize: 12,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              background:
                                selectedTag === t.name ? token.colorPrimaryBg : 'transparent',
                              color: selectedTag === t.name ? token.colorPrimary : token.colorText,
                              fontWeight: selectedTag === t.name ? 600 : 400,
                              transition: 'all 0.15s',
                            }}
                            onMouseEnter={(e) => {
                              if (selectedTag !== t.name)
                                e.currentTarget.style.background = token.colorFillQuaternary;
                            }}
                            onMouseLeave={(e) => {
                              if (selectedTag !== t.name)
                                e.currentTarget.style.background = 'transparent';
                            }}
                          >
                            <span
                              style={{
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {t.name}
                            </span>
                            <span
                              style={{
                                fontSize: 10,
                                color: token.colorTextTertiary,
                                flexShrink: 0,
                                marginLeft: 6,
                              }}
                            >
                              {t.count}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Collapsed sidebar toggle */}
              {tagSidebarCollapsed && (
                <div
                  style={{
                    width: 36,
                    flexShrink: 0,
                    borderRight: `1px solid ${token.colorBorderSecondary}`,
                    background: token.colorBgLayout,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    paddingTop: 10,
                    gap: 8,
                  }}
                >
                  <Tooltip title="展开标签面板" placement="right">
                    <Button
                      type="text"
                      size="small"
                      icon={<MenuUnfoldOutlined />}
                      onClick={() => setTagSidebarCollapsed(false)}
                      style={{ color: token.colorTextTertiary }}
                    />
                  </Tooltip>
                  {allTags.slice(0, 8).map((t) => (
                    <Tooltip key={t.name} title={`${t.name} (${t.count})`} placement="right">
                      <div
                        onClick={() => handleFocusTag(t.name)}
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background:
                            selectedTag === t.name
                              ? token.colorPrimary
                              : token.colorBorderSecondary,
                          cursor: 'pointer',
                          flexShrink: 0,
                          transition: 'background 0.15s',
                        }}
                      />
                    </Tooltip>
                  ))}
                </div>
              )}

              {/* Graph canvas — fills all remaining space */}
              <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
                <MemoryGraph searchQuery={query} onSelectMemory={handleSelectMemory} />

                {/* Detail panel — absolute overlay on right side */}
                <AnimatePresence>
                  {selectedMemoryId && (
                    <>
                      {/* Backdrop */}
                      <motion.div
                        key="detail-backdrop"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        onClick={handleCloseDetail}
                        style={{
                          position: 'absolute',
                          inset: 0,
                          background: 'rgba(0,0,0,0.15)',
                          zIndex: 10,
                        }}
                      />
                      {/* Panel */}
                      <motion.div
                        key="detail-panel"
                        initial={{ x: 340, opacity: 0 }}
                        animate={{ x: 0, opacity: 1 }}
                        exit={{ x: 340, opacity: 0 }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        style={{
                          position: 'absolute',
                          top: 4,
                          right: 4,
                          bottom: 4,
                          zIndex: 11,
                          borderRadius: 12,
                          overflow: 'hidden',
                          boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
                        }}
                      >
                        <MemoryDetailPanel
                          memoryId={selectedMemoryId}
                          onClose={handleCloseDetail}
                          onFocusTag={handleFocusTag}
                          onSelectMemory={handleSelectMemory}
                        />
                      </motion.div>
                    </>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
