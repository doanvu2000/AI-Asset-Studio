import type {
  ApiErrorBody,
  BatchCreateBody,
  BlobRef,
  ExportPreset,
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
  StepRow,
  VersionRow,
  VersionStatus
} from './types'

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly issues: RecipeIssue[] = []
  ) {
    super(message)
  }
}

export interface ListRunsQuery {
  limit?: number
  before?: number
  status?: RunStatus
}

export interface StudioApi {
  health(): Promise<{ ok: boolean; version: string }>
  resources(): Promise<ResourceHealth[]>
  startResource(name: string): Promise<ResourceHealth>
  stopResource(name: string): Promise<ResourceHealth>
  recipes(): Promise<RecipeInfo[]>
  startRun(body: StartRunBody): Promise<{ run_id: string }>
  listRuns(q?: ListRunsQuery): Promise<RunRow[]>
  getRun(id: string): Promise<RunDetail>
  cancelRun(id: string): Promise<void>
  retryStep(stepId: string): Promise<void>
  gates(): Promise<StepRow[]>
  resolveGate(stepId: string, res: GateResolution): Promise<void>
  uploadBlob(file: File): Promise<BlobRef>
  versions(q?: { run_id?: string; status?: VersionStatus }): Promise<VersionRow[]>
  setVersionStatus(id: string, action: 'approve' | 'reject'): Promise<VersionRow>
  blobUrl(hash: string): string
  thumbUrl(hash: string, size?: number): string
  kinds(): Promise<KindInfo[]>
  getRecipe(id: string): Promise<RecipeDetail>
  validateRecipe(body: RecipeBody): Promise<ValidateResult>
  saveRecipe(id: string, body: RecipeBody): Promise<SavedRecipe>
  deleteRecipe(id: string): Promise<{ ok: boolean; restored_builtin: boolean }>
  startBatch(body: BatchCreateBody): Promise<{ batch_id: string; run_ids: string[] }>
  presets(): Promise<ExportPreset[]>
  savePreset(p: ExportPreset): Promise<ExportPreset>
  deletePreset(id: string): Promise<void>
}

export type RecipeBody = { yaml: string } | { doc: RecipeDoc }

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export function createHttpApi(baseUrl: string, token: string): StudioApi {
  const auth = { Authorization: `Bearer ${token}` }

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response
    try {
      res = await fetch(`${baseUrl}${path}`, {
        method,
        headers: body instanceof FormData ? auth : { ...auth, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body)
      })
    } catch {
      throw new ApiError('network', 'Không kết nối được tới core', 0)
    }
    if (!res.ok) {
      let code = `http_${res.status}`
      let message = res.statusText || 'Lỗi không rõ'
      let issues: RecipeIssue[] = []
      try {
        const err = (await res.json()) as ApiErrorBody
        code = err.error?.code ?? code
        message = err.error?.message ?? message
        issues = err.error?.issues ?? []
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(code, message, res.status, issues)
    }
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  const enc = encodeURIComponent
  return {
    health: () => call('GET', '/api/health'),
    resources: () => call('GET', '/api/resources'),
    startResource: (name) => call('POST', `/api/resources/${enc(name)}/start`),
    stopResource: (name) => call('POST', `/api/resources/${enc(name)}/stop`),
    recipes: () => call('GET', '/api/recipes'),
    startRun: (body) => call('POST', '/api/runs', body),
    listRuns: (q = {}) => call('GET', `/api/runs${query({ limit: q.limit, before: q.before, status: q.status })}`),
    getRun: (id) => call('GET', `/api/runs/${enc(id)}`),
    cancelRun: (id) => call('POST', `/api/runs/${enc(id)}/cancel`),
    retryStep: (id) => call('POST', `/api/steps/${enc(id)}/retry`),
    gates: () => call('GET', '/api/gates'),
    resolveGate: (id, res) => call('POST', `/api/gates/${enc(id)}`, res),
    uploadBlob(file) {
      const fd = new FormData()
      fd.append('file', file, file.name)
      return call('POST', '/api/blobs', fd)
    },
    versions: (q = {}) => call('GET', `/api/versions${query({ run_id: q.run_id, status: q.status })}`),
    setVersionStatus: (id, action) => call('POST', `/api/versions/${enc(id)}/${action}`),
    blobUrl: (hash) => `${baseUrl}/api/blobs/${enc(hash)}?t=${enc(token)}`,
    thumbUrl: (hash, size = 256) => `${baseUrl}/api/blobs/${enc(hash)}/thumb?size=${size}&t=${enc(token)}`,
    kinds: () => call('GET', '/api/kinds'),
    getRecipe: (id) => call('GET', `/api/recipes/${enc(id)}`),
    validateRecipe: (body) => call('POST', '/api/recipes/validate', body),
    saveRecipe: (id, body) => call('PUT', `/api/recipes/${enc(id)}`, body),
    deleteRecipe: (id) => call('DELETE', `/api/recipes/${enc(id)}`),
    startBatch: (body) => call('POST', '/api/batches', body),
    presets: () => call('GET', '/api/presets'),
    savePreset: (p) => call('PUT', `/api/presets/${enc(p.id)}`, p),
    deletePreset: (id) => call('DELETE', `/api/presets/${enc(id)}`)
  }
}
