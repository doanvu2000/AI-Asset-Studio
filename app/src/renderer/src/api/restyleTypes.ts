export type RestyleKind = 'sprite' | 'sheet' | 'ui' | 'tile' | 'texture'
export type RestyleEngine = 'unity' | 'godot' | 'none'

export interface RestyleScanItem {
  rel: string
  kind: RestyleKind
  width: number
  height: number
  sprites: number
  /** left, top, right, bottom (px); all 0 = no 9-slice. */
  border: [number, number, number, number]
  importer: string
  guid: string
  units: number
}

export interface RestyleScan {
  root: string
  engine: RestyleEngine
  project: string
  counts: Record<RestyleKind, number>
  units: number
  items: RestyleScanItem[]
  skipped: { rel: string; why: string }[]
  skipped_total: number
  estimate: { edit_s: number; measured: number; trial_s: number; trial_units: number; full_s: number }
  mirror: string
}

/** One row of restyle.json (op.restyle_fit). */
export interface RestyleReportItem {
  rel: string
  kind: RestyleKind
  width: number
  height: number
  src: string
  new_hash: string
  guid: string
  importer: string
  what?: string | null
  group?: string | null
  matte: 'original' | 'birefnet'
  alpha_iou: number
  slice_fixed: string[]
  seam: [number, number]
  anchor_shift: number
  size_ok: boolean
  units: number
  qa: { score: number | null; grade: string | null; issues: { label: string; message: string; severity: string }[] }
}

export interface RestyleReport {
  type: string
  root: string
  engine: RestyleEngine
  total: number
  sample: number
  style: string
  items: RestyleReportItem[]
  qa: { summary: { score: number | null; flagged: number; counts: Record<string, number> } }
  flags: { silhouette: string[]; low_qa: string[]; size: string[] }
}

export interface RestyleApplyResult {
  mode: string
  root: string
  written: number
  skipped: number
  backup_id?: string
  backup?: string
  importers_unchanged?: boolean
  files: { rel: string; action: 'write' | 'skip'; why?: string }[]
}

export interface RestyleUndoResult {
  backup_id: string
  restored: string[]
  conflicts: { rel: string; why: string }[]
}

export interface RestyleBackup {
  backup_id: string
  root: string
  at: number
  written: number
  undone_at: number | null
  source: { run_id?: string }
}
