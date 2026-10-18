export type Lane = 'gpu' | 'blender' | 'cloud' | 'cpu' | 'none'

export type StepStatus =
  | 'pending'
  | 'ready'
  | 'waiting_resource'
  | 'running'
  | 'awaiting_human'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'cached'
  | 'skipped'

export const STEP_TERMINAL: ReadonlySet<StepStatus> = new Set(['succeeded', 'failed', 'cancelled', 'cached', 'skipped'])
export const STEP_DONE_OK: ReadonlySet<StepStatus> = new Set(['succeeded', 'cached', 'skipped'])

export type RunStatus = 'running' | 'awaiting_human' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export const RUN_ACTIVE: ReadonlySet<RunStatus> = new Set(['running', 'awaiting_human'])

export type VersionStatus = 'draft' | 'approved' | 'rejected'

export interface BlobRef {
  hash: string
  mime: string
  size: number
  width?: number | null
  height?: number | null
  name?: string | null
}

export type Outputs = Record<string, BlobRef[]>

export interface StepErrorInfo {
  code: string
  message: string
  detail?: unknown
  retryable?: boolean
}

export interface RunRow {
  id: string
  project_id: string
  recipe_id: string
  recipe_hash: string
  title: string
  inputs: Record<string, unknown>
  params: Record<string, unknown>
  status: RunStatus
  error: string | null
  created_at: number
  ended_at: number | null
}

export interface StepMeta {
  waiting_for?: Lane | string
  [k: string]: unknown
}

export interface StepRow {
  id: string
  run_id: string
  key: string
  kind: string
  lane: Lane
  status: StepStatus
  attempt: number
  deps: string[]
  params: Record<string, unknown>
  inputs: Outputs
  outputs: Outputs
  cache_key: string | null
  error: StepErrorInfo | null
  progress: number | null
  meta: StepMeta
  order: number
  started_at: number | null
  ended_at: number | null
}

export interface VersionRow {
  id: string
  project_id: string
  asset_name: string
  run_id: string
  step_id: string
  blob: BlobRef
  status: VersionStatus
  created_at: number
}

export type EventType =
  | 'run.created'
  | 'run.updated'
  | 'step.updated'
  | 'step.progress'
  | 'step.log'
  | 'step.preview'
  | 'gate.awaiting'
  | 'version.updated'
  | 'resource.updated'

export interface StudioEvent<T = Record<string, unknown>> {
  seq: number
  ts: number
  type: EventType
  run_id: string | null
  step_id: string | null
  data: T
}

export interface RunUpdatedData {
  id?: string
  status: RunStatus
  error: string | null
  ended_at: number | null
}

export interface ProgressData {
  value: number
  max: number
  label?: string | null
  node?: string | null
}

export interface LogData {
  level: string
  msg: string
}

export interface PreviewData {
  mime: string
  b64: string
}

export interface GateAwaitingData {
  kind: 'pick' | 'approve'
  // pick: 0 means "at least one"; approve gates always send 0.
  k?: number
  candidates: BlobRef[]
}

export type ResourceState = 'down' | 'starting' | 'up' | 'busy' | 'error'

export interface ResourceHealth {
  name: string
  status: ResourceState
  detail: {
    vram_free_mb?: number
    vram_total_mb?: number
    version?: string
    pid?: number
    managed?: boolean
    message?: string
    [k: string]: unknown
  }
}

export type RecipeInputType = 'text' | 'int' | 'float' | 'bool' | 'image' | 'images' | 'mesh' | 'enum' | 'select' | string

export interface RecipeInputSpec {
  type: RecipeInputType
  default?: unknown
  label?: string
  min?: number
  max?: number
  step?: number
  options?: Array<string | number>
  help?: string
  required?: boolean
}

export interface RecipeStepInfo {
  id: string
  kind: string
  lane: Lane
  deps?: string[]
  map?: string | null
}

export interface RecipeInfo {
  id: string
  name: string
  description: string
  inputs: Record<string, RecipeInputSpec>
  steps: RecipeStepInfo[]
  hash: string
}

export interface GateResolution {
  picked?: string[] | null
  approved?: boolean | null
}

export interface RunDetail {
  run: RunRow
  steps: StepRow[]
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}

export interface StartRunBody {
  recipe_id: string
  inputs: Record<string, unknown>
  params?: Record<string, Record<string, unknown>>
  title?: string
  project_id?: string
}
