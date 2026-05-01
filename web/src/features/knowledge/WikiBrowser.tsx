import { useEffect, useState, useCallback } from 'react';
import { Tree, Input, Spin, Empty, Typography, Card, Tag, Breadcrumb, App } from 'antd';
import { SearchOutlined, FolderOutlined, FileTextOutlined, LinkOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import api from '@/services/api';

const { Text, Title } = Typography;

interface WikiTreeNode {
  name: string;
  title: string;
  path: string;
  type: 'directory' | 'page';
  count?: number;
  children: WikiTreeNode[];
}

interface WikiPageData {
  name: string;
  title: string;
  content: string;
  type: string;
  tags: string[];
  sources: string[];
  created: string;
  updated: string;
  links_to: string[];
  linked_from: string[];
  word_count: number;
  size: number;
}

interface Props {
  initialPage?: string;
}

export default function WikiBrowser({ initialPage }: Props) {
  const { message: msg } = App.useApp();
  const [tree, setTree] = useState<WikiTreeNode[]>([]);
  const [treeLoading, setTreeLoading] = useState(true);
  const [page, setPage] = useState<WikiPageData | null>(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [searchFilter, setSearchFilter] = useState('');

  const fetchTree = useCallback(async () => {
    setTreeLoading(true);
    try {
      const res = await api.get('/knowledge/wiki');
      setTree((res.data as WikiTreeNode).children || []);
    } catch {
      msg.error('加载 Wiki 目录失败');
    } finally {
      setTreeLoading(false);
    }
  }, [msg]);

  const fetchPage = useCallback(
    async (pageName: string) => {
      setPageLoading(true);
      try {
        const res = await api.get(`/knowledge/wiki/${encodeURIComponent(pageName)}`);
        setPage(res.data as WikiPageData);
      } catch {
        msg.error('加载页面失败');
      } finally {
        setPageLoading(false);
      }
    },
    [msg],
  );

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  useEffect(() => {
    if (initialPage) {
      setSelectedKeys([initialPage]);
      fetchPage(initialPage);
    }
  }, [initialPage, fetchPage]);

  const handleTreeSelect = (keys: React.Key[]) => {
    if (keys.length > 0) {
      const key = String(keys[0]);
      setSelectedKeys([key]);
      fetchPage(key);
    }
  };

  // Build Ant Tree data from WikiTreeNode
  const treeData = tree.map((group) => ({
    key: group.path,
    title: (
      <span style={{ fontSize: 13, fontWeight: 600 }}>
        <FolderOutlined style={{ marginRight: 6, color: '#1677ff' }} />
        {group.title}
        <span style={{ color: '#999', marginLeft: 6, fontSize: 12 }}>({group.count})</span>
      </span>
    ),
    selectable: false,
    children: group.children
      .filter(
        (child) => !searchFilter || child.title.toLowerCase().includes(searchFilter.toLowerCase()),
      )
      .map((child) => ({
        key: child.name,
        title: (
          <span style={{ fontSize: 13 }}>
            <FileTextOutlined style={{ marginRight: 6, color: '#666' }} />
            {child.title}
          </span>
        ),
        isLeaf: true,
      })),
  }));

  // ── Wikilink handling ───────────────────────────────────────────

  function processWikilinks(node: React.ReactNode): React.ReactNode {
    if (typeof node !== 'string') {
      if (Array.isArray(node)) {
        return (node as React.ReactNode[]).map((child, i) => (
          <span key={i}>{processWikilinks(child)}</span>
        ));
      }
      return node;
    }

    const parts = node.split(/(\[\[[^\]]+\]\])/g);
    return parts.map((part, i) => {
      const match = part.match(/^\[\[([^\]]+)\]\]$/);
      if (match) {
        const target = match[1].split('|')[0].trim();
        const display = match[1].includes('|') ? match[1].split('|')[1].trim() : target;
        return (
          <a
            key={i}
            style={{
              color: '#1677ff',
              cursor: 'pointer',
              textDecoration: 'underline',
              fontWeight: 500,
            }}
            onClick={(e) => {
              e.preventDefault();
              setSelectedKeys([target]);
              fetchPage(target);
            }}
          >
            {display}
          </a>
        );
      }
      return <span key={i}>{part}</span>;
    });
  }

  const headingRenderer = (level: 1 | 2 | 3 | 4) =>
    function Heading({ children }: any) {
      const sizes = { 1: 22, 2: 18, 3: 16, 4: 15 };
      const margins = { 1: 24, 2: 20, 3: 16, 4: 12 };
      return (
        <Title
          level={level as 1 | 2 | 3 | 4}
          style={{ marginTop: margins[level], fontSize: sizes[level] }}
        >
          {processWikilinks(children)}
        </Title>
      );
    };

  return (
    <div
      style={{
        display: 'flex',
        gap: 16,
        height: 'calc(100vh - 200px)',
        minHeight: 500,
      }}
    >
      {/* ── Left: Tree ────────────────────────────────────── */}
      <Card
        size="small"
        style={{ width: 300, minWidth: 260, overflow: 'auto', flexShrink: 0 }}
        styles={{ body: { padding: 8 } }}
      >
        <div style={{ marginBottom: 8, padding: '0 4px' }}>
          <Input
            size="small"
            placeholder="过滤页面..."
            prefix={<SearchOutlined />}
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            allowClear
          />
        </div>
        {treeLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : (
          <Tree
            treeData={treeData}
            selectedKeys={selectedKeys}
            onSelect={handleTreeSelect}
            showIcon={false}
            blockNode
            style={{ fontSize: 13 }}
          />
        )}
      </Card>

      {/* ── Right: Viewer ─────────────────────────────────── */}
      <Card size="small" style={{ flex: 1, overflow: 'auto' }} styles={{ body: { padding: 24 } }}>
        {pageLoading ? (
          <div style={{ textAlign: 'center', padding: 80 }}>
            <Spin size="large" />
          </div>
        ) : !page ? (
          <Empty description="选择一个页面开始浏览" style={{ marginTop: 80 }} />
        ) : (
          <div style={{ maxWidth: 860, margin: '0 auto' }}>
            <Breadcrumb
              style={{ marginBottom: 16 }}
              items={[
                { title: 'Wiki' },
                ...(page.type ? [{ title: <Tag>{page.type}</Tag> }] : []),
                { title: page.title },
              ]}
            />

            {page.tags.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                {page.tags.map((tag) => (
                  <Tag key={tag} style={{ borderRadius: 4 }}>
                    {tag}
                  </Tag>
                ))}
              </div>
            )}

            <div style={{ marginBottom: 20, fontSize: 12, color: '#999' }}>
              {page.created && <span>创建: {page.created}</span>}
              {page.updated && <span style={{ marginLeft: 12 }}>更新: {page.updated}</span>}
              <span style={{ marginLeft: 12 }}>{page.word_count} 词</span>
              <span style={{ marginLeft: 12 }}>{(page.size / 1024).toFixed(1)} KB</span>
            </div>

            <div className="wiki-content" style={{ lineHeight: 1.8, fontSize: 15 }}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  h1: headingRenderer(1),
                  h2: headingRenderer(2),
                  h3: headingRenderer(3),
                  h4: headingRenderer(4),
                  p: ({ children }) => <p>{processWikilinks(children)}</p>,
                  li: ({ children }) => <li>{processWikilinks(children)}</li>,
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote
                      style={{
                        borderLeft: '3px solid #1677ff',
                        paddingLeft: 12,
                        color: '#666',
                        margin: '12px 0',
                      }}
                    >
                      {children}
                    </blockquote>
                  ),
                  code: ({ className, children }: any) =>
                    className ? (
                      <pre
                        style={{
                          background: '#1a1a2e',
                          color: '#e0e0e0',
                          padding: 16,
                          borderRadius: 8,
                          overflow: 'auto',
                        }}
                      >
                        <code className={className}>{children}</code>
                      </pre>
                    ) : (
                      <code
                        style={{
                          background: '#f0f0f0',
                          padding: '2px 6px',
                          borderRadius: 4,
                          fontSize: 13,
                        }}
                      >
                        {children}
                      </code>
                    ),
                  table: ({ children }) => (
                    <div style={{ overflow: 'auto', margin: '12px 0' }}>
                      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                        {children}
                      </table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th
                      style={{
                        border: '1px solid #e0e0e0',
                        padding: '8px 12px',
                        background: '#fafafa',
                        textAlign: 'left',
                      }}
                    >
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td style={{ border: '1px solid #e0e0e0', padding: '8px 12px' }}>{children}</td>
                  ),
                }}
              >
                {page.content}
              </ReactMarkdown>
            </div>

            {/* Sources */}
            {page.sources.length > 0 && (
              <div
                style={{
                  marginTop: 32,
                  padding: 16,
                  background: '#f5f5f5',
                  borderRadius: 8,
                }}
              >
                <Text type="secondary" style={{ fontSize: 12 }}>
                  来源文件:
                </Text>
                <div style={{ marginTop: 4 }}>
                  {page.sources.map((src) => (
                    <Tag key={src} style={{ fontSize: 11 }}>
                      {src}
                    </Tag>
                  ))}
                </div>
              </div>
            )}

            {/* Backlinks */}
            {page.linked_from.length > 0 && (
              <div
                style={{
                  marginTop: 16,
                  padding: 16,
                  background: '#f5f5f5',
                  borderRadius: 8,
                }}
              >
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <LinkOutlined style={{ marginRight: 4 }} />
                  引用本页的页面 ({page.linked_from.length}):
                </Text>
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {page.linked_from.map((link) => (
                    <Tag
                      key={link}
                      color="blue"
                      style={{ cursor: 'pointer', borderRadius: 4 }}
                      onClick={() => {
                        setSelectedKeys([link]);
                        fetchPage(link);
                      }}
                    >
                      {link}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
