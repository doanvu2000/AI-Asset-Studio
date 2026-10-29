import { reactive } from 'vue'
import type { HitBinding } from '../../../preload/types'

/** Per sprite-sheet step: the hit VFX picked in the dialog, applied by every later Unity/Godot export of that step. */
export interface SavedHit {
  enabled: boolean
  vfx_step: string
  vfx_name: string
  frames: Record<string, number>
  points: Record<string, [number, number]>
  size: number
}

const KEY = (stepId: string): string => `export:hits:${stepId}`
const cache = reactive(new Map<string, SavedHit | null>())

function read(stepId: string): SavedHit | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY(stepId)) ?? 'null') as Partial<SavedHit> | null
    if (!v || typeof v.vfx_step !== 'string') return null
    return { enabled: v.enabled ?? true, vfx_step: v.vfx_step, vfx_name: v.vfx_name ?? '', frames: v.frames ?? {}, points: v.points ?? {}, size: v.size ?? 0.5 }
  } catch {
    return null
  }
}

export function savedHit(stepId: string): SavedHit | null {
  if (!cache.has(stepId)) cache.set(stepId, read(stepId))
  return cache.get(stepId) ?? null
}

export function saveHit(stepId: string, v: SavedHit | null): void {
  cache.set(stepId, v)
  try {
    if (v) localStorage.setItem(KEY(stepId), JSON.stringify(v))
    else localStorage.removeItem(KEY(stepId))
  } catch {
    /* storage blocked: the binding lasts for this session only */
  }
}

export function hitExportOption(stepId: string): HitBinding | undefined {
  const v = savedHit(stepId)
  if (!v?.enabled) return undefined
  return { vfx_step: v.vfx_step, frames: { ...v.frames }, points: { ...v.points }, size: v.size }
}
