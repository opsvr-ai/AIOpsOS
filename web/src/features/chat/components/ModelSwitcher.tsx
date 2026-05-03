import { useEffect, useRef, useState, useMemo } from 'react';
import { Select, Tag, Typography } from 'antd';
import api from '@/services/api';

interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  model_name: string;
  model_type: string;
  is_default: boolean;
  is_active: boolean;
}

interface Props {
  value: string | null;
  onChange: (id: string | null) => void;
}

const TYPE_LABELS: Record<string, string> = {
  llm: 'LLM',
  multimodal: '多模态',
  voice: '语音',
  embedding: '嵌入',
  rerank: '重排序',
};

const TYPE_COLORS: Record<string, string> = {
  llm: 'blue',
  multimodal: 'purple',
  voice: 'orange',
  embedding: 'green',
  rerank: 'cyan',
};

const TYPE_ORDER = ['llm', 'multimodal', 'voice', 'embedding', 'rerank'];

export default function ModelSwitcher({ value, onChange }: Props) {
  const [models, setModels] = useState<ModelProvider[]>([]);
  const autoSelected = useRef(false);

  useEffect(() => {
    api
      .get('/model-providers')
      .then((res) => {
        const data: ModelProvider[] = res.data || [];
        const active = data.filter((m) => m.is_active);
        setModels(active);
        if (!autoSelected.current && !value && active.length > 0) {
          autoSelected.current = true;
          const def = active.find((m) => m.is_default) || active[0];
          onChange(def.id);
        }
      })
      .catch(() => {});
  }, []);

  const groupedOptions = useMemo(() => {
    const groups: Record<string, ModelProvider[]> = {};
    for (const m of models) {
      const t = m.model_type || 'llm';
      (groups[t] ||= []).push(m);
    }
    return TYPE_ORDER.filter((t) => groups[t]?.length).map((t) => ({
      label: TYPE_LABELS[t] || t,
      options: groups[t].map((m) => ({
        value: m.id,
        label: m.name || m.model_name,
        model: m,
      })),
    }));
  }, [models]);

  return (
    <Select
      value={value ?? undefined}
      onChange={(v) => onChange(v ?? null)}
      placeholder="选择模型"
      size="small"
      style={{ minWidth: 200 }}
      options={groupedOptions}
      optionRender={(option) => {
        const m = (option.data as { model?: ModelProvider })?.model;
        if (!m) return option.label;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag color={TYPE_COLORS[m.model_type] || 'default'} style={{ fontSize: 10 }}>
              {m.provider_type}
            </Tag>
            <Typography.Text strong>{m.name || m.model_name}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {m.model_name}
            </Typography.Text>
          </div>
        );
      }}
      dropdownStyle={{ minWidth: 280 }}
      popupMatchSelectWidth={false}
    />
  );
}
