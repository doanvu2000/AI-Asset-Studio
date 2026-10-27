import type {
  ApiErrorBody,
  BatchCreateBody,
  BlobRef,
  ExportPreset,
  ProjectCreateBody,
  ProjectPatchBody,
  ProjectRow,
  ProjectSummary,
  ProjectWithSummary,
  ToolKey,
  ToolPaths,
  KindInfo,
  RecipeDetail,
  RecipeDoc,
  RecipeIssue,
  SavedRecipe,
  ValidateResult,
  GateResolution,
  RecipeInfo,
  ResourceHealth,
  RunDetail,
  RunRow,
  RunStatus,
  StartRunBody,
  StatsOverview,
  StatsQuery,
  StepRow,
  VersionRow,
  VersionStatus,
  QaRecomputeResult,
  SetupJob,
  SetupPlan,
  SetupStartBody
} from './types'
import type { CoreDiagnostics } from '../../../preload/types'
import { devlog } from '@/composables/devlog'

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly issues: RecipeIssue[] = [],
    readonly detail: Record<string, unknown> = {}
  ) {
    super(message)
  }
}

export interface ListRunsQuery {
  limit?: number
  before?: number
  status?: RunStatus
  batch_id?: string
  batched?: boolean
  project_id?: string
}

export interface VersionsQuery {
  run_id?: string
  status?: VersionStatus
  project_id?: string
}

export interface StudioApi {
  health(): Promise<{ ok: boolean; version: string }>
  resources(): Promise<ResourceHealth[]>
  startResource(name: string): Promise<ResourceHealth>
  /** `force` stops a tool even while it is busy (core answers 409 resource_busy otherwise). */
  stopResource(name: string, force?: boolean): Promise<ResourceHealth>
  recipes(): Promise<RecipeInfo[]>
  startRun(body: StartRunBody): Promise<{ run_id: string }>
  listRuns(q?: ListRunsQuery): Promise<RunRow[]>
  getRun(id: string): Promise<RunDetail>
  cancelRun(id: string): Promise<void>
  retryStep(stepId: string): Promise<void>
  gates(): Promise<StepRow[]>
  resolveGate(stepId: string, res: GateResolution): Promise<void>
  uploadBlob(file: File): Promise<BlobRef>
  versions(q?: VersionsQuery): Promise<VersionRow[]>
  setVersionStatus(id: string, action: 'approve' | 'reject'): Promise<VersionRow>
  blobUrl(hash: string): string
  thumbUrl(hash: string, size?: number): string
  kinds(): Promise<KindInfo[]>
  getRecipe(id: string): Promise<RecipeDetail>
  validateRecipe(body: RecipeBody): Promise<ValidateResult>
  saveRecipe(id: string, body: RecipeBody): Promise<SavedRecipe>
  deleteRecipe(id: string): Promise<{ ok: boolean; restored_builtin: boolean }>
  startBatch(body: BatchCreateBody): Promise<{ batch_id: string; run_ids: string[] }>
  /** Vietnamese texts → English via Claude (others pass through), same order; cached per text in the core. */
  translate(texts: string[]): Promise<{ texts: string[] }>
  presets(): Promise<ExportPreset[]>
  savePreset(p: ExportPreset): Promise<ExportPreset>
  deletePreset(id: string): Promise<void>
  toolPaths(versions?: boolean): Promise<ToolPaths>
  saveToolPaths(paths: Partial<Record<ToolKey, string>>): Promise<ToolPaths>
  diagnostics(): Promise<CoreDiagnostics>
  projects(q?: { archived?: boolean }): Promise<ProjectWithSummary[]>
  getProject(id: string): Promise<ProjectRow>
  createProject(body: ProjectCreateBody): Promise<ProjectRow>
  /** `style` replaces the whole style object. */
  patchProject(id: string, body: ProjectPatchBody): Promise<ProjectRow>
  archiveProject(id: string): Promise<ProjectRow>
  projectSummary(id: string): Promise<ProjectSummary>
  /** Cost/time per recipe + per-day totals + most expensive step kinds for one period. */
  statsOverview(q?: StatsQuery): Promise<StatsOverview>
  /** Scores Library versions that have no QA yet (or all of them with `missing_only: false`). */
  recomputeQa(body?: { run_id?: string; project_id?: string; missing_only?: boolean; limit?: number }): Promise<QaRecomputeResult>
  recomputeVersionQa(id: string): Promise<VersionRow>
  /** What one-click setup would install; dirs default to the core's suggestion. */
  setupPlan(dirs?: { tools_dir?: string; models_dir?: string }): Promise<{ plan: SetupPlan; job: SetupJob | null }>
  setupJob(): Promise<SetupJob | null>
  startSetup(body: SetupStartBody): Promise<SetupJob>
  cancelSetup(): Promise<SetupJob | null>
  claudeLogin(): Promise<void>
}

export type RecipeBody = { yaml: string } | { doc: RecipeDoc }

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

const TIMEOUT_MS = 20_000
// Uploads, Claude translation and batch fan-out legitimately take longer than a plain read.
const SLOW_TIMEOUT_MS = 180_000

export function createHttpApi(baseUrl: string, token: string): StudioApi {
  const auth = { Authorization: `Bearer ${token}` }

  async function call<T>(method: string, path: string, body?: unknown, timeoutMs = TIMEOUT_MS): Promise<T> {
    let res: Response
    try {
      res = await fetch(`${baseUrl}${path}`, {
        method,
        headers: body instanceof FormData ? auth : { ...auth, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs)
      })
    } catch (e) {
      const err = e as Error
      devlog('warn', `api ${method} ${path} → network: ${err.message}`)
      if (err.name === 'TimeoutError') throw new ApiError('timeout', 'Core phản hồi quá lâu, thử lại sau', 0)
      throw new ApiError('network', 'Không kết nối được tới core', 0)
    }
    if (!res.ok) {
      let code = `http_${res.status}`
      let message = res.statusText || 'Lỗi không rõ'
      let issues: RecipeIssue[] = []
      let detail: Record<string, unknown> = {}
      try {
        const err = (await res.json()) as ApiErrorBody
        code = err.error?.code ?? code
        message = err.error?.message ?? message
        issues = err.error?.issues ?? []
        detail = err.error?.detail ?? {}
      } catch {
        /* non-JSON error body */
      }
      devlog('warn', `api ${method} ${path} → ${res.status} ${code}: ${message}`)
      throw new ApiError(code, message, res.status, issues, detail)
    }
    if (res.status === 204) return undefined as T
    try {
      return (await res.json()) as T
    } catch (e) {
      // The abort signal also covers the body stream, so a slow body surfaces here, not in fetch().
      if ((e as Error).name === 'TimeoutError') throw new ApiError('timeout', 'Core phản hồi quá lâu, thử lại sau', 0)
      throw new ApiError('bad_response', 'Core trả về dữ liệu không đọc được', res.status)
    }
  }

  const enc = encodeURIComponent
  return {
    health: () => call('GET', '/api/health'),
    resources: () => call('GET', '/api/resources'),
    startResource: (name) => call('POST', `/api/resources/${enc(name)}/start`, undefined, SLOW_TIMEOUT_MS),
    stopResource: (name, force) => call('POST', `/api/resources/${enc(name)}/stop${force ? '?force=true' : ''}`, undefined, SLOW_TIMEOUT_MS),
    recipes: () => call('GET', '/api/recipes'),
    startRun: (body) => call('POST', '/api/runs', body, SLOW_TIMEOUT_MS),
    listRuns: (q = {}) =>
      call(
        'GET',
        `/api/runs${query({ limit: q.limit, before: q.before, status: q.status, batch_id: q.batch_id, batched: q.batched ? 'true' : undefined, project_id: q.project_id })}`,
        undefined,
        q.batched || (q.limit ?? 0) > 500 ? SLOW_TIMEOUT_MS : TIMEOUT_MS
      ),
    getRun: (id) => call('GET', `/api/runs/${enc(id)}`),
    cancelRun: (id) => call('POST', `/api/runs/${enc(id)}/cancel`),
    retryStep: (id) => call('POST', `/api/steps/${enc(id)}/retry`),
    gates: () => call('GET', '/api/gates'),
    resolveGate: (id, res) => call('POST', `/api/gates/${enc(id)}`, res),
    uploadBlob(file) {
      const fd = new FormData()
      fd.append('file', file, file.name)
      return call('POST', '/api/blobs', fd, SLOW_TIMEOUT_MS)
    },
    versions: (q = {}) => call('GET', `/api/versions${query({ run_id: q.run_id, status: q.status, project_id: q.project_id })}`),
    setVersionStatus: (id, action) => call('POST', `/api/versions/${enc(id)}/${action}`),
    blobUrl: (hash) => `${baseUrl}/api/blobs/${enc(hash)}?t=${enc(token)}`,
    thumbUrl: (hash, size = 256) => `${baseUrl}/api/blobs/${enc(hash)}/thumb?size=${size}&t=${enc(token)}`,
    kinds: () => call('GET', '/api/kinds'),
    getRecipe: (id) => call('GET', `/api/recipes/${enc(id)}`),
    validateRecipe: (body) => call('POST', '/api/recipes/validate', body),
    saveRecipe: (id, body) => call('PUT', `/api/recipes/${enc(id)}`, body),
    deleteRecipe: (id) => call('DELETE', `/api/recipes/${enc(id)}`),
    startBatch: (body) => call('POST', '/api/batches', body, SLOW_TIMEOUT_MS),
    translate: (texts) => call('POST', '/api/translate', { texts }, SLOW_TIMEOUT_MS),
    presets: () => call('GET', '/api/presets'),
    savePreset: (p) => call('PUT', `/api/presets/${enc(p.id)}`, p),
    deletePreset: (id) => call('DELETE', `/api/presets/${enc(id)}`),
    toolPaths: (versions = true) => call('GET', `/api/settings/tools${query({ versions: versions ? undefined : 'false' })}`, undefined, SLOW_TIMEOUT_MS),
    saveToolPaths: (paths) => call('PUT', '/api/settings/tools', { paths }),
    diagnostics: () => call('GET', '/api/diagnostics', undefined, SLOW_TIMEOUT_MS),
    setupPlan: (dirs = {}) => call('GET', `/api/setup${query(dirs)}`, undefined, SLOW_TIMEOUT_MS),
    setupJob: () => call('GET', '/api/setup/job'),
    startSetup: (body) => call('POST', '/api/setup/start', body),
    cancelSetup: () => call('POST', '/api/setup/cancel'),
    claudeLogin: async () => {
      await call('POST', '/api/setup/claude-login')
    },
    projects: (q = {}) => call('GET', `/api/projects${query({ archived: q.archived ? 'true' : 'false', summary: 'true' })}`),
    getProject: (id) => call('GET', `/api/projects/${enc(id)}`),
    createProject: (body) => call('POST', '/api/projects', body),
    patchProject: (id, body) => call('PATCH', `/api/projects/${enc(id)}`, body),
    archiveProject: (id) => call('DELETE', `/api/projects/${enc(id)}`),
    projectSummary: (id) => call('GET', `/api/projects/${enc(id)}/summary`),
    statsOverview: (q = {}) =>
      call('GET', `/api/stats/overview${query({ since: q.since, until: q.until, project_id: q.project_id, days: q.days, tz_offset_s: q.tz_offset_s })}`),
    recomputeQa: (body = {}) => call('POST', '/api/qa/recompute', body, SLOW_TIMEOUT_MS),
    recomputeVersionQa: (id) => call('POST', `/api/qa/versions/${enc(id)}`, undefined, SLOW_TIMEOUT_MS)
  }
}
