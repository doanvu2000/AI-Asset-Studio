export type CoreState = 'stopped' | 'starting' | 'ready' | 'restarting' | 'error'

export interface CoreStatus {
  state: CoreState
  attempt: number
  baseUrl?: string
  pid?: number
  message?: string
  detail?: string
  retryInMs?: number
  mock?: boolean
  attached?: boolean
}

export interface CoreConnection {
  baseUrl: string
  token: string
}

export interface McpInfo {
  command: string
  args: string[]
  coreDir: string
  packaged: boolean
}

export interface StudioBridge {
  getConnection(): Promise<CoreConnection>
  mcpInfo(): Promise<McpInfo>
  copyText(text: string): Promise<void>
  onCoreStatus(cb: (s: CoreStatus) => void): () => void
  log(level: RendererLogLevel, message: string): void
  diagInfo(): Promise<DiagInfo>
  copyDiagnostics(payload: DiagPayload): Promise<CopyResult>
  exportDiagnostics(payload: DiagPayload): Promise<ExportResult | null>
  openLogsDir(): Promise<void>
  platform: string
}

export interface LogFileInfo {
  name: string
  bytes: number
  modified: number
}

export interface DiagInfo {
  appVersion: string
  electron: string
  chrome: string
  node: string
  platform: string
  arch: string
  osRelease: string
  packaged: boolean
  portable: boolean
  mock: boolean
  dataDir: string
  logsDir: string
  core: CoreStatus
  logs: LogFileInfo[]
}

export interface DiagFailedStep {
  run_id: string
  step_id: string
  key: string
  kind: string
  attempt: number
  code: string | null
  message: string
  ended_at: number | null
}

export interface CoreDiagnostics {
  generated_at: number
  core: { version: string; pid: number; started_at: number; frozen: boolean; python: string; executable: string; platform: string }
  data_dir: string
  settings_file: string
  tools: Array<{ key: string; label: string; path: string; exists: boolean; ok: boolean; detail: string | null }>
  resources: Array<{ name: string; status: string; detail: Record<string, unknown> }>
  counts: { recipes: number | null; kinds: number | null }
  failed_steps: DiagFailedStep[]
  logs: Array<{ name: string; path: string; bytes?: number; modified?: number; error?: string; lines: string[] }>
}

/** What the renderer hands to main for a report: the core snapshot, or why it could not be fetched. */
export interface DiagPayload {
  diagnostics: CoreDiagnostics | null
  error?: string
}

export interface CopyResult {
  chars: number
}

export interface ExportResult {
  path: string
  files: number
  bytes: number
}

export type RendererLogLevel = 'info' | 'warn' | 'error'
