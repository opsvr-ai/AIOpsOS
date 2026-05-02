import type { A2UIMessage, Surface, ResolvedComponent, PathValue, CallExpression } from './types';

export class A2UIMessageProcessor {
  private surfaces = new Map<string, Surface>();
  private listeners = new Set<() => void>();

  processMessages(messages: A2UIMessage[]): void {
    for (const msg of messages) {
      if ('createSurface' in msg) {
        this.surfaces.set(msg.createSurface.surfaceId, {
          surfaceId: msg.createSurface.surfaceId,
          catalogId: msg.createSurface.catalogId,
          components: new Map(),
          dataModel: {},
          rootId: null,
        });
      } else if ('updateComponents' in msg) {
        const surface = this.getOrCreateSurface(msg.updateComponents.surfaceId);
        for (const comp of msg.updateComponents.components) {
          surface.components.set(comp.id, comp);
          if (!surface.rootId) surface.rootId = comp.id;
        }
      } else if ('updateDataModel' in msg) {
        const surface = this.getOrCreateSurface(msg.updateDataModel.surfaceId);
        const path = msg.updateDataModel.path || '/';
        if (path === '/') {
          // Wrap root-level data under "data" key so /data/field paths resolve
          surface.dataModel = { data: msg.updateDataModel.value };
        } else {
          setAtPath(surface.dataModel, path, msg.updateDataModel.value);
        }
      } else if ('deleteSurface' in msg) {
        this.surfaces.delete(msg.deleteSurface.surfaceId);
      }
    }
    this.notify();
  }

  getSurface(surfaceId: string): Surface | undefined {
    return this.surfaces.get(surfaceId);
  }

  getSurfaces(): Surface[] {
    return [...this.surfaces.values()];
  }

  setRoot(surfaceId: string, rootId: string): void {
    const surface = this.surfaces.get(surfaceId);
    if (surface) surface.rootId = rootId;
  }

  resolveTree(surfaceId: string): ResolvedComponent | null {
    const surface = this.surfaces.get(surfaceId);
    if (!surface || !surface.rootId) return null;
    return this.resolveComponent(surface, surface.rootId);
  }

  resolvePath(surfaceId: string, path: string): unknown {
    const surface = this.surfaces.get(surfaceId);
    if (!surface) return undefined;
    return resolveDataPath(surface.dataModel, path);
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  clear(): void {
    this.surfaces.clear();
    this.notify();
  }

  private resolveComponent(surface: Surface, compId: string): ResolvedComponent | null {
    const def = surface.components.get(compId);
    if (!def) return null;

    const properties: Record<string, unknown> = {};
    const children: ResolvedComponent[] = [];

    for (const [key, value] of Object.entries(def)) {
      if (key === 'id' || key === 'component' || key === 'weight') continue;

      if (key === 'child' && typeof value === 'string') {
        const child = this.resolveComponent(surface, value);
        if (child) children.push(child);
      } else if (key === 'children' && Array.isArray(value)) {
        for (const childId of value) {
          if (typeof childId === 'string') {
            const child = this.resolveComponent(surface, childId);
            if (child) children.push(child);
          }
        }
      } else if (isPathValue(value)) {
        properties[key] = resolveDataPath(surface.dataModel, value.path);
      } else if (isCallExpression(value)) {
        properties[key] = this.evaluateCall(surface, value);
      } else {
        properties[key] = value;
      }
    }

    return { id: compId, type: def.component, weight: def.weight, properties, children };
  }

  private evaluateCall(surface: Surface, expr: CallExpression): unknown {
    const fns: Record<string, (args: Record<string, unknown>) => unknown> = {
      formatString: ({ value }) => {
        if (typeof value !== 'string') return value;
        return value.replace(/\$\{([^}]+)\}/g, (_: string, inner: string) => {
          const trimmed = inner.trim();
          if (trimmed.startsWith('/')) {
            return String(resolveDataPath(surface.dataModel, trimmed) ?? '');
          }
          const m = trimmed.match(/^(\w+)\((.*)\)$/);
          if (m) {
            const result = this.evaluateCall(surface, { call: m[1], args: parseCallArgs(m[2]) });
            return String(result ?? '');
          }
          return `\${${trimmed}}`;
        });
      },
      formatDate: ({ value, format: fmt }) => {
        try {
          const raw = resolveValue(surface, value);
          const d = new Date(typeof raw === 'string' ? raw : '');
          if (isNaN(d.getTime())) return String(raw ?? '');
          const localeFmt: Intl.DateTimeFormatOptions = {};
          const f = String(fmt || 'EEEE, MMMM d');
          if (f.includes('EEEE')) localeFmt.weekday = 'long';
          if (f.includes('MMMM')) localeFmt.month = 'long';
          if (f.includes('d')) localeFmt.day = 'numeric';
          if (f.includes('yyyy')) localeFmt.year = 'numeric';
          return d.toLocaleDateString('en-US', localeFmt);
        } catch {
          return String(value ?? '');
        }
      },
      email: ({ value }) =>
        /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(resolveValue(surface, value) ?? '')),
      regex: ({ value, pattern }) => {
        try {
          return new RegExp(String(pattern)).test(String(resolveValue(surface, value) ?? ''));
        } catch {
          return false;
        }
      },
      required: ({ value }) => {
        const v = resolveValue(surface, value);
        return v !== null && v !== undefined && v !== '';
      },
      and: ({ values }) => {
        if (!Array.isArray(values)) return false;
        return values.every((v: unknown) => resolveValue(surface, v));
      },
      or: ({ values }) => {
        if (!Array.isArray(values)) return false;
        return values.some((v: unknown) => resolveValue(surface, v));
      },
    };
    const fn = fns[expr.call];
    if (!fn) return null;
    try {
      return fn(expr.args);
    } catch {
      return null;
    }
  }

  private getOrCreateSurface(surfaceId: string): Surface {
    if (!this.surfaces.has(surfaceId)) {
      this.surfaces.set(surfaceId, {
        surfaceId,
        components: new Map(),
        dataModel: {},
        rootId: null,
      });
    }
    return this.surfaces.get(surfaceId)!;
  }

  private notify(): void {
    this.listeners.forEach((fn) => fn());
  }
}

// ── Helpers ──

export function isPathValue(v: unknown): v is PathValue {
  return (
    typeof v === 'object' && v !== null && 'path' in v && Object.keys(v as object).length === 1
  );
}

export function isCallExpression(v: unknown): v is CallExpression {
  return typeof v === 'object' && v !== null && 'call' in v;
}

export function resolveDataPath(obj: Record<string, unknown>, path: string): unknown {
  if (typeof path !== 'string') return undefined;
  const parts = path.replace(/^\//, '').split('/');
  let current: unknown = obj;
  for (const part of parts) {
    if (!part) continue;
    if (current && typeof current === 'object') {
      current = (current as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return current;
}

function resolveValue(surface: Surface, val: unknown): unknown {
  if (isPathValue(val)) return resolveDataPath(surface.dataModel, val.path);
  if (isCallExpression(val)) return null;
  return val;
}

function setAtPath(obj: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.replace(/^\//, '').split('/').filter(Boolean);
  if (parts.length === 0) return;
  let current: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (
      !(parts[i] in current) ||
      typeof current[parts[i]] !== 'object' ||
      current[parts[i]] === null
    ) {
      current[parts[i]] = {};
    }
    current = current[parts[i]] as Record<string, unknown>;
  }
  const last = parts[parts.length - 1];
  current[last] = value;
}

function parseCallArgs(s: string): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const pairs = s.split(/,(?![^{]*\})/);
  for (const pair of pairs) {
    const m = pair.match(/^\s*(\w+)\s*:\s*(.+)\s*$/);
    if (m) {
      const val = m[2].trim();
      if (val.startsWith("'") || val.startsWith('"')) {
        result[m[1]] = val.slice(1, -1);
      } else if (val === 'true' || val === 'false') {
        result[m[1]] = val === 'true';
      } else if (!isNaN(Number(val))) {
        result[m[1]] = Number(val);
      } else {
        result[m[1]] = val;
      }
    }
  }
  return result;
}
