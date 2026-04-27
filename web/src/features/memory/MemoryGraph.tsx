import { useMemo, useCallback, useRef, useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { Button, Space, Tooltip, Spin, Empty, theme } from 'antd';
import {
  ZoomInOutlined,
  ZoomOutOutlined,
  ExpandOutlined,
  AimOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { useMemoryStore, type GraphNode } from '@/stores/memoryStore';
import { useThemeStore } from '@/stores/themeStore';

interface MemoryGraphProps {
  searchQuery?: string;
  onSelectMemory: (id: string) => void;
}

const COLORS = {
  tag: '#f0a500',
  tagHover: '#ffc53d',
  personal: '#4a90d9',
  personalHover: '#69b1ff',
  team: '#52c41a',
  teamHover: '#73d13d',
};

export default function MemoryGraph({ searchQuery, onSelectMemory }: MemoryGraphProps) {
  const { token } = theme.useToken();
  const isDark = useThemeStore((s) => s.mode) === 'dark';
  const { graphData, graphLoading, selectedTag, focusTag, clearFocus } = useMemoryStore();
  const chartRef = useRef<ReactECharts>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [zoomLevel, setZoomLevel] = useState(1);

  // Resize chart when container size changes (sidebar toggle, detail panel, etc.)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      const chart = chartRef.current?.getEchartsInstance();
      if (chart && !chart.isDisposed()) {
        chart.resize();
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const handleZoomIn = () => {
    const chart = chartRef.current?.getEchartsInstance();
    if (chart) {
      const option = chart.getOption() as { series?: Array<{ zoom?: number }> };
      const current = option.series?.[0]?.zoom ?? 1;
      const next = Math.min(current * 1.3, 5);
      chart.dispatchAction({ type: 'restore' });
      chart.setOption({ series: [{ zoom: next }] });
      setZoomLevel(next);
    }
  };

  const handleZoomOut = () => {
    const chart = chartRef.current?.getEchartsInstance();
    if (chart) {
      const option = chart.getOption() as { series?: Array<{ zoom?: number }> };
      const current = option.series?.[0]?.zoom ?? 1;
      const next = Math.max(current / 1.3, 0.2);
      chart.setOption({ series: [{ zoom: next }] });
      setZoomLevel(next);
    }
  };

  const handleReset = () => {
    const chart = chartRef.current?.getEchartsInstance();
    if (chart) {
      chart.dispatchAction({ type: 'restore' });
      setZoomLevel(1);
    }
  };

  const handleFit = () => {
    clearFocus();
    handleReset();
  };

  const bgColor = isDark ? '#1a1a2e' : '#fafbfc';
  const textColor = isDark ? '#e0e0e0' : '#333';
  const edgeColor = isDark ? '#333' : '#d9d9d9';

  const option = useMemo(() => {
    if (!graphData || graphData.nodes.length === 0) return null;

    const searchLower = (searchQuery ?? '').toLowerCase();

    return {
      backgroundColor: bgColor,
      tooltip: {
        formatter: (params: { data?: GraphNode; dataType?: string }) => {
          if (params.dataType === 'edge' || !params.data) return '';
          const d = params.data;
          if (d.type === 'tag') {
            return `<strong style="color:${COLORS.tag}">🏷 ${d.label}</strong><br/>${d.count ?? 0} 条记忆`;
          }
          const scopeLabel = d.scope === 'team' ? '组织' : '个人';
          const scopeColor = d.scope === 'team' ? COLORS.team : COLORS.personal;
          return `<strong>${d.label}</strong><br/><span style="color:${scopeColor}">${scopeLabel}记忆</span>`;
        },
        backgroundColor: isDark ? '#2d2d2d' : '#fff',
        borderColor: token.colorBorderSecondary,
        textStyle: { color: textColor, fontSize: 12 },
      },
      animationDuration: 500,
      animationEasingUpdate: 'cubicInOut',
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          zoom: zoomLevel,
          force: {
            repulsion: searchQuery ? 400 : 250,
            gravity: 0.08,
            edgeLength: [60, 200],
            layoutAnimation: true,
            friction: 0.6,
          },
          scaleLimit: { min: 0.2, max: 5 },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 3 },
            itemStyle: { shadowBlur: 20, shadowColor: 'rgba(0,0,0,0.3)' },
          },
          edgeSymbol: ['none', 'none'],
          lineStyle: {
            color: edgeColor,
            curveness: 0.1,
            opacity: 0.35,
            width: 1,
          },
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            color: textColor,
            fontFamily: token.fontFamily,
          },
          categories: [
            {
              name: 'tag',
              itemStyle: {
                color: COLORS.tag,
                borderColor: isDark ? '#8b6914' : '#d4a017',
                borderWidth: 1.5,
              },
              symbol: 'circle',
            },
            {
              name: 'personal',
              itemStyle: {
                color: COLORS.personal,
                borderColor: isDark ? '#2a5a8a' : '#3a7bc8',
                borderWidth: 1.5,
              },
              symbol: 'pin',
              symbolSize: 36,
            },
            {
              name: 'team',
              itemStyle: {
                color: COLORS.team,
                borderColor: isDark ? '#2a6a10' : '#3d9a14',
                borderWidth: 1.5,
              },
              symbol: 'pin',
              symbolSize: 36,
            },
          ],
          data: graphData.nodes.map((n) => {
            const category = n.type === 'tag' ? 'tag' : n.scope === 'team' ? 'team' : 'personal';
            const isDimmed =
              searchQuery &&
              !n.label.toLowerCase().includes(searchLower) &&
              !n.id.includes(searchLower);

            return {
              id: n.id,
              name: n.label,
              category,
              symbolSize:
                n.type === 'tag' ? Math.max(28, Math.min((n.count ?? 1) * 5 + 18, 64)) : 36,
              itemStyle: isDimmed ? { opacity: 0.15 } : { opacity: 1 },
              label: isDimmed
                ? { show: false }
                : { show: true, color: textColor, fontSize: n.type === 'tag' ? 11 : 10 },
              _raw: n,
            };
          }),
          links: graphData.edges.map((e) => ({
            source: e.source,
            target: e.target,
            lineStyle: searchQuery ? { opacity: 0.1 } : { opacity: 0.35 },
          })),
        },
      ],
    };
  }, [graphData, searchQuery, isDark, bgColor, textColor, edgeColor, token, zoomLevel]);

  const onChartClick = useCallback(
    (params: { data?: { id?: string; _raw?: GraphNode } }) => {
      const raw = params.data?._raw;
      if (!raw) return;
      if (raw.type === 'tag') {
        focusTag(raw.label);
      } else if (raw.type === 'memory') {
        onSelectMemory(raw.id.replace('mem-', ''));
      }
    },
    [focusTag, onSelectMemory],
  );

  const onEvents = useMemo(() => ({ click: onChartClick }), [onChartClick]);

  // --- render ---
  if (graphLoading) {
    return (
      <div
        ref={containerRef}
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: bgColor,
          borderRadius: 12,
          border: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Spin indicator={<LoadingOutlined style={{ fontSize: 28 }} spin />} tip="加载图谱数据..." />
      </div>
    );
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div
        ref={containerRef}
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: bgColor,
          borderRadius: 12,
          border: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Empty
          description={
            <span style={{ color: token.colorTextTertiary, fontSize: 13 }}>
              暂无记忆数据。开始对话后，智能体会自动提取并构建知识图谱。
            </span>
          }
        />
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ position: 'absolute', inset: 0 }}>
      {/* Floating toolbar */}
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          right: 16,
          zIndex: 10,
          background: isDark ? 'rgba(30,30,30,0.9)' : 'rgba(255,255,255,0.9)',
          borderRadius: 10,
          padding: '4px 6px',
          backdropFilter: 'blur(8px)',
          border: `1px solid ${token.colorBorderSecondary}`,
          boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
        }}
      >
        <Space size={2}>
          <Tooltip title="放大">
            <Button
              type="text"
              size="small"
              icon={<ZoomInOutlined />}
              onClick={handleZoomIn}
              style={{ color: token.colorTextTertiary }}
            />
          </Tooltip>
          <Tooltip title="缩小">
            <Button
              type="text"
              size="small"
              icon={<ZoomOutOutlined />}
              onClick={handleZoomOut}
              style={{ color: token.colorTextTertiary }}
            />
          </Tooltip>
          <Tooltip title="重置">
            <Button
              type="text"
              size="small"
              icon={<ExpandOutlined />}
              onClick={handleReset}
              style={{ color: token.colorTextTertiary }}
            />
          </Tooltip>
          <Tooltip title="全景">
            <Button
              type="text"
              size="small"
              icon={<AimOutlined />}
              onClick={handleFit}
              style={{ color: token.colorTextTertiary }}
            />
          </Tooltip>
        </Space>
      </div>

      {/* Tag filter chip */}
      {selectedTag && (
        <div
          style={{
            position: 'absolute',
            top: 12,
            left: 12,
            zIndex: 10,
            background: COLORS.tag,
            color: '#fff',
            padding: '4px 12px',
            borderRadius: 16,
            fontSize: 12,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            cursor: 'pointer',
            boxShadow: '0 2px 6px rgba(240,165,0,0.3)',
          }}
          onClick={handleFit}
        >
          🏷 {selectedTag}
          <span style={{ marginLeft: 2, opacity: 0.8, fontSize: 10 }}>✕</span>
        </div>
      )}

      {/* Empty overlay when search yields nothing */}
      {searchQuery &&
        graphData.nodes.every(
          (n) => !n.label.toLowerCase().includes(searchQuery.toLowerCase()),
        ) && (
          <div
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              zIndex: 10,
              background: isDark ? 'rgba(30,30,30,0.85)' : 'rgba(255,255,255,0.85)',
              padding: '12px 24px',
              borderRadius: 8,
              fontSize: 13,
              color: token.colorTextTertiary,
            }}
          >
            未找到匹配 &quot;{searchQuery}&quot; 的节点
          </div>
        )}

      <ReactECharts
        ref={chartRef}
        option={option}
        style={{ width: '100%', height: '100%', borderRadius: 12 }}
        onEvents={onEvents}
        notMerge
        lazyUpdate
        opts={{ renderer: 'canvas' }}
      />
    </div>
  );
}
