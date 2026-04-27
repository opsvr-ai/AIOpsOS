import { useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Button, theme } from 'antd';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import type { Components } from 'react-markdown';

function CodeBlock({ language, code }: { language: string; code: string }) {
  const { token } = theme.useToken();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* Clipboard API not available */
    }
  }, [code]);

  return (
    <div
      className="chat-code-block"
      style={{
        margin: '8px 0',
        borderRadius: 8,
        overflow: 'hidden',
        border: `1px solid ${token.colorBorderSecondary}`,
        fontSize: 13,
      }}
    >
      <div
        style={{
          padding: '4px 12px',
          fontSize: 11,
          color: token.colorTextTertiary,
          background: token.colorFillQuaternary,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span>{language || 'text'}</span>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined style={{ color: token.colorSuccess }} /> : <CopyOutlined />}
          onClick={handleCopy}
          style={{ fontSize: 12, height: 22 }}
        >
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: '12px 16px',
          overflow: 'auto',
          background: token.colorFillQuaternary,
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function MarkdownContent({ children }: { children: string }) {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isDark = mode === 'dark';

  const components: Components = {
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className ?? '');
      const code = String(children).replace(/\n$/, '');
      if (match) {
        return <CodeBlock language={match[1]} code={code} />;
      }
      return (
        <code
          style={{
            background: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
            padding: '1px 5px',
            borderRadius: 4,
            fontSize: '0.9em',
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            wordBreak: 'break-word',
          }}
          {...props}
        >
          {children}
        </code>
      );
    },
    pre({ children }) {
      return <>{children}</>;
    },
    table({ children }) {
      return (
        <div style={{ overflowX: 'auto', margin: '8px 0' }}>
          <table
            style={{
              borderCollapse: 'collapse',
              width: '100%',
              fontSize: 13,
            }}
          >
            {children}
          </table>
        </div>
      );
    },
    th({ children }) {
      return (
        <th
          style={{
            border: `1px solid ${token.colorBorder}`,
            padding: '6px 10px',
            background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
            fontWeight: 600,
            textAlign: 'left',
          }}
        >
          {children}
        </th>
      );
    },
    td({ children }) {
      return (
        <td
          style={{
            border: `1px solid ${token.colorBorder}`,
            padding: '6px 10px',
          }}
        >
          {children}
        </td>
      );
    },
    a({ href, children }) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: token.colorPrimary, textDecoration: 'underline' }}
        >
          {children}
        </a>
      );
    },
    hr() {
      return (
        <hr
          style={{
            border: 'none',
            borderTop: `1px solid ${token.colorBorder}`,
            margin: '16px 0',
          }}
        />
      );
    },
    img({ src, alt }) {
      if (!src) return null;
      return (
        <div style={{ margin: '12px 0' }}>
          <img
            src={src}
            alt={alt ?? ''}
            style={{
              maxWidth: '100%',
              maxHeight: 400,
              borderRadius: 8,
              border: `1px solid ${token.colorBorder}`,
              objectFit: 'contain',
            }}
            loading="lazy"
          />
          {alt && (
            <div
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                marginTop: 4,
                textAlign: 'center',
              }}
            >
              {alt}
            </div>
          )}
        </div>
      );
    },
    blockquote({ children }) {
      return (
        <blockquote
          style={{
            margin: '8px 0',
            padding: '4px 12px',
            borderLeft: `3px solid ${token.colorPrimary}`,
            color: token.colorTextSecondary,
            background: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)',
            borderRadius: '0 4px 4px 0',
          }}
        >
          {children}
        </blockquote>
      );
    },
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={components}
    >
      {children}
    </ReactMarkdown>
  );
}
