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
  return r.ok ? `Đã xuất “${what}” vào ${r.path ?? r.dest_dir}` : `Không xuất được “${what}”: ${r.error ?? 'lỗi không rõ'}`
}
