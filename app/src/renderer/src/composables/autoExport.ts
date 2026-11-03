import { shallowRef } from 'vue'
import type { ProjectPatchBody, ProjectRow } from '@/api/types'

// Local until api/types.ts carries these fields (patch in the WP-12E report).
export interface AutoExportFields {
  auto_export?: boolean
  auto_export_dir?: string
}
export type ProjectWithAutoExport = ProjectRow & AutoExportFields
export type AutoExportPatch = ProjectPatchBody & AutoExportFields

export interface AutoExportResult {
  project_id: string
  version_id: string
  asset_name: string
  engine: string
  dest_dir: string
  ok: boolean
  path?: string
  files?: number
  error?: string
  code?: string
  ts: number
}

export const AUTO_EXPORT_EVENT = 'project.auto_export'

const last = shallowRef<Record<string, AutoExportResult>>({})

export function lastAutoExport(projectId: string): AutoExportResult | undefined {
  return last.value[projectId]
}

export function recordAutoExport(data: Record<string, unknown>, ts: number): AutoExportResult {
  const r = { ...(data as unknown as Omit<AutoExportResult, 'ts'>), ts }
  last.value = { ...last.value, [r.project_id]: r }
  return r
}

export function autoExportText(r: AutoExportResult): string {
  const what = r.asset_name || 'asset'
  if (r.ok) return `Đã xuất “${what}” vào ${r.path ?? r.dest_dir}`
  if (r.code === 'nothing_to_export') return `“${what}” chưa có gì để xuất cho engine này (cần sprite sheet, GLB, texture, âm thanh…)`
  // Core texts for these codes are English; the rest (folder, engine) already come in Vietnamese.
  if (r.code === 'bundle_error') return `Không xuất được “${what}”: dữ liệu không khớp với engine đích`
  return `Không xuất được “${what}”: ${r.error ?? 'lỗi không rõ'}`
}

export interface AutoExportToast {
  text: string
  kind: 'info' | 'success' | 'error'
  ms: number
}

const MERGE_MS = 1500
const recent = new Map<string, number>()

/** Approving one card approves several versions in a burst; the same outcome for the same folder shows once. */
export function autoExportToast(r: AutoExportResult, now: number = Date.now()): AutoExportToast | null {
  const key = `${r.project_id}|${r.ok}|${r.code ?? ''}|${r.path ?? r.dest_dir}|${r.ok ? '' : r.asset_name}`
  const at = recent.get(key)
  recent.set(key, now)
  for (const [k, t] of recent) if (now - t > MERGE_MS) recent.delete(k)
  if (at !== undefined && now - at <= MERGE_MS) return null
  const text = autoExportText(r)
  if (r.ok) return { text, kind: 'success', ms: 5000 }
  return { text, kind: r.code === 'nothing_to_export' ? 'info' : 'error', ms: 8000 }
}
