export type AuditEngine = 'unity' | 'godot' | 'flutter'
export type AuditFixType = 'mobile_preset' | 'max_size' | 'drop_alpha' | 'atlas' | 'flutter_declare' | 'flutter_variant' | 'flutter_webp' | 'flutter_font'

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

/** Flutter project finding (pubspec assets, resolution variants, heavy images). */
export interface AuditFinding {
  code: 'asset_missing' | 'asset_undeclared' | 'variant_missing' | 'image_big'
  severity: 'error' | 'warn' | 'info'
  rel: string
  msg: string
  bytes?: number
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
    duplicate_method?: 'clip' | 'phash' | 'off'
    unreferenced: number
    unreferenced_bytes: number
    meshes: number
    meshes_over: number
    audio: number
    issues: Record<string, number>
    flutter?: { assets: number; declared: number; big_bytes: number; errors: number }
  }
  findings?: AuditFinding[]
  textures: AuditTexture[]
  duplicates: { kind: 'exact' | 'near'; files: string[]; wasted_bytes: number; method?: 'clip' | 'phash' }[]
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
  bytes_before?: number
  bytes_after?: number
  backup_id?: string
  /** Dry run only: pass back to apply so the core reuses the plan instead of planning again. */
  plan_id?: string
  cancelled?: boolean
  /** Set with `cancelled`: at least one file was written, so the last report is stale. */
  rescan_needed?: boolean
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
  bytes_before?: number | null
  bytes_after?: number | null
}
