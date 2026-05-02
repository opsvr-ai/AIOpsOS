/** Top-level A2UI v0.9 message — exactly one action property */
export type A2UIMessage =
  | { createSurface: CreateSurface }
  | { updateComponents: UpdateComponents }
  | { updateDataModel: UpdateDataModel }
  | { deleteSurface: DeleteSurface };

export interface CreateSurface {
  surfaceId: string;
  catalogId?: string;
  sendDataModel?: boolean;
}

export interface UpdateComponents {
  surfaceId: string;
  components: ComponentDef[];
}

export interface UpdateDataModel {
  surfaceId: string;
  path?: string;
  value: Record<string, unknown>;
}

export interface DeleteSurface {
  surfaceId: string;
}

// ── Component Definition ──

export interface ComponentDef {
  id: string;
  component: string;
  weight?: number;
  [prop: string]: unknown;
}

// ── Data Binding ──

export interface PathValue {
  path: string;
}

export interface CallExpression {
  call: string;
  args: Record<string, unknown>;
  returnType?: string;
}

// ── Client Event ──

export interface A2UIClientEvent {
  surfaceId: string;
  name: string;
  sourceComponentId: string;
  context: Record<string, unknown>;
  timestamp: number;
}

// ── Resolved Surface ──

export interface ResolvedComponent {
  id: string;
  type: string;
  weight?: number;
  properties: Record<string, unknown>;
  children: ResolvedComponent[];
}

export interface Surface {
  surfaceId: string;
  catalogId?: string;
  components: Map<string, ComponentDef>;
  dataModel: Record<string, unknown>;
  rootId: string | null;
}
