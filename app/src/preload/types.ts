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
  /** Opens <data>/settings.json in the default editor (created as `{}` when missing); the way to fix paths while the core is down. */
  openSettingsFile(): Promise<void>
  restartCore(): Promise<void>
  pickPath(opts: PickPathOptions): Promise<string | null>
  asset: AssetBridge
  /** Taskbar progress: 0..1, or null to clear. `paused` paints it amber (waiting on the user). */
  setProgress(value: number | null, mode?: ProgressMode): void
  /** Shown only while the window is not focused; clicking it focuses the app and navigates to `route`. */
  notify(n: AppNotification): void
  onNavigate(cb: (route: string) => void): () => void
  platform: string
}

export type ProgressMode = 'normal' | 'paused' | 'error' | 'indeterminate'

export interface AppNotification {
  title: string
  body: string
  route?: string
}

export interface PickPathOptions {
  kind: 'file' | 'dir'
  title?: string
  defaultPath?: string
  /** Extensions without dots, e.g. ['exe']. */
  extensions?: string[]
}

export interface ExportedAsset {
  hash: string
  path: string
  name: string
  mime: string
  size: number
}

/** Every call takes a blob hash plus an optional display file name; main resolves the file itself. */
export interface AssetBridge {
  prepare(hash: string, name?: string): Promise<ExportedAsset>
  saveAs(hash: string, name?: string): Promise<string | null>
  reveal(hash: string, name?: string): Promise<void>
  copyPath(hash: string, name?: string): Promise<string>
  startDrag(hash: string, name?: string): void
  /** Folder exports of a whole step (Spine json+atlas+png, sprite sheet repacked for an engine preset). */
  bundleOptions(stepId: string): Promise<BundleOptions>
  saveBundle(stepId: string, preset?: string, name?: string): Promise<string | null>
  revealBundle(stepId: string, preset?: string, name?: string): Promise<void>
}

export interface BundleOptions {
  step_id: string
  kind: string
  files: number
  sprite_sheet: boolean
  /** Preset the recipe already packed the sheet with, if any. */
  preset: string | null
}

export interface ExportedBundle {
  dir: string
  name: string
  preset: string | null
  files: number
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
