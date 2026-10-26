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
  // Only on GET /runs: latest image output, for list thumbnails.
  preview_hash?: string | null
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
  | 'project.updated'

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

export type RecipeInputType = 'preset' | 'text' | 'int' | 'float' | 'bool' | 'image' | 'images' | 'mesh' | 'enum' | 'select' | string

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
  source?: RecipeSource
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
  error: { code: string; message: string; issues?: RecipeIssue[] }
}

export interface StartRunBody {
  recipe_id: string
  inputs: Record<string, unknown>
  params?: Record<string, Record<string, unknown>>
  title?: string
  project_id?: string
}

export type RecipeSource = 'builtin' | 'user' | 'override'

// 'image[]' etc.; '*' in a port map means the step passes through whatever it receives.
export type PortType = string

export interface ParamSpec extends RecipeInputSpec {
  multiline?: boolean
  advanced?: boolean
  code?: boolean
  node?: boolean
}

export interface KindInfo {
  kind: string
  lane: Lane
  label: string
  in: Record<string, PortType> | null
  out: Record<string, PortType> | null
  params: Record<string, ParamSpec>
  free_params: boolean
}

export type Binding = string | string[]

export interface RecipeStepDoc {
  id: string
  kind: string
  in?: Record<string, Binding>
  params?: Record<string, unknown>
  map?: string | null
  map_param?: string | null
}

export interface RecipeDoc {
  id: string
  name?: string
  description?: string
  inputs?: Record<string, RecipeInputSpec>
  steps: RecipeStepDoc[]
  outputs?: Record<string, string>
  ui?: { positions?: Record<string, [number, number]> }
  [k: string]: unknown
}

export interface RecipeDetail {
  info: RecipeInfo
  source: RecipeSource
  has_builtin: boolean
  yaml: string
  doc: RecipeDoc
}

export interface RecipeIssue {
  level: 'error' | 'warning'
  step: string | null
  field: string | null
  message: string
}

export interface ValidateResult {
  ok: boolean
  issues: RecipeIssue[]
  info: RecipeInfo | null
}

export interface SavedRecipe {
  info: RecipeInfo
  source: RecipeSource
  has_builtin: boolean
  issues?: RecipeIssue[]
}

export type Compass = 'S' | 'SW' | 'W' | 'NW' | 'N' | 'NE' | 'E' | 'SE'

export interface ExportPreset {
  id: string
  name: string
  description: string
  dir_order: Compass[]
  dir_labels: Partial<Record<Compass, string>>
  file_pattern: string
  layout: 'row' | 'column' | 'grid'
  cols: number
  padding: number
  pivot_origin: 'top_left' | 'bottom_left'
  builtin: boolean
}

export interface BatchItem {
  inputs: Record<string, unknown>
  title?: string
}

export interface BatchCreateBody {
  recipe_id: string
  items: BatchItem[]
  inputs?: Record<string, unknown>
  title?: string
  project_id?: string
}

export interface RunMeta {
  source?: string
  batch_id?: string
  batch_index?: number
  batch_total?: number
  batch_title?: string
  [k: string]: unknown
}

export const NO_PROJECT = 'default'

export interface PixelStyle {
  size?: number | null
  palette?: string | null
  colors?: number | null
  outline?: string | null
  dither?: string | null
}

/** Locked look of a project, English text; the core fills it into recipe inputs the user left empty. */
export interface ProjectStyle {
  prompt_prefix: string
  prompt_suffix: string
  negative: string
  preset: string | null
  pixel: PixelStyle | null
  palette_image: string | null
  reference_image: string | null
  inputs: Record<string, unknown>
  notes: string
}

export interface ProjectRow {
  id: string
  name: string
  description: string
  style: ProjectStyle
  created_at: number
  updated_at: number
  archived: boolean
}

export interface ProjectSummary {
  project_id: string
  runs: Partial<Record<RunStatus, number>>
  runs_total: number
  versions: Partial<Record<VersionStatus, number>>
  approved_versions: number
  last_activity: number | null
  cover_hash: string | null
}

export interface ProjectWithSummary extends ProjectRow {
  summary?: ProjectSummary
}

export interface ProjectCreateBody {
  name: string
  description?: string
  style?: Partial<ProjectStyle>
}

export interface ProjectPatchBody {
  name?: string
  description?: string
  style?: ProjectStyle
  archived?: boolean
}

export type ToolKey = 'comfy_dir' | 'comfy_python' | 'comfy_models_dir' | 'comfy_workflows_dir' | 'blender_exe' | 'codex_exe'

export interface ToolPath {
  key: ToolKey
  label: string
  kind: 'dir' | 'file'
  path: string
  exists: boolean
  ok: boolean
  version: string | null
  detail: string | null
}

export interface ToolPaths {
  tools: ToolPath[]
  settings_file: string
}
