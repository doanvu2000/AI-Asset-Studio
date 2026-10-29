export type AuditEngine = 'unity' | 'godot'
export type AuditFixType = 'mobile_preset' | 'max_size' | 'drop_alpha' | 'atlas'

export interface AuditFix {
  type: AuditFixType
  rel?: string
  rels?: string[]
  kind?: string
  max_size?: number
  name?: string
}

export interface AuditIssue {
  code: string
  severity: 'warn' | 'info'
  msg: string
  fix?: AuditFix
}

export interface AuditTexture {
  rel: string
  w: number
  h: number
  bytes: number
  kind: string | null
  alpha_channel: boolean
  alpha_used: boolean | null
  importer: string
  display: [number, number] | null
  gpu_before: number
  gpu_after: number
  issues: AuditIssue[]
}

export interface AuditReport {
  root: string
  project: string
  engine: AuditEngine
  at: number
  seconds: number
  tri_budget: number
  summary: {
    textures: number
    texture_bytes: number
    gpu_before: number
    gpu_after: number
    fixable: number
    duplicate_groups: number
    duplicate_bytes: number
    unreferenced: number
    unreferenced_bytes: number
    meshes: number
    meshes_over: number
    audio: number
    issues: Record<string, number>
  }
  textures: AuditTexture[]
  duplicates: { kind: 'exact' | 'near'; files: string[]; wasted_bytes: number }[]
  unreferenced: { rel: string; kind: string; bytes: number }[]
  meshes: { rel: string; bytes: number; tris: number | null; over: boolean; error?: string }[]
  audio: { rel: string; bytes: number; seconds: number | null; issues: AuditIssue[] }[]
  notes: string[]
}

export interface AuditFixResult {
  dry_run?: boolean
  written?: number
  skipped?: number
  gpu_before?: number
  gpu_after?: number
  backup_id?: string
  files?: { rel: string; action: 'write' | 'skip'; why?: string; fixes?: string[] }[]
  atlases?: { folder?: string; files?: string[]; planned?: boolean }[]
  report?: AuditReport
}

export interface AuditBackup {
  backup_id: string
  project: string
  engine: AuditEngine
  at: number
  written: number
  undone_at: number | null
  gpu_before: number | null
  gpu_after: number | null
}
