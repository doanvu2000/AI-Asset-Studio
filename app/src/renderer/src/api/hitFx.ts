import type { BlobRef } from './types'

/** One attack track (clip × direction) with its detected impact frame and per-frame impact points (cell px). */
export interface HitTrack {
  track: string
  clip: string
  dir: string
  angle: number
  row: number
  frames: number
  fps: number
  frame: number
  auto_frame: number
  /** meta = clip metadata, rig = procedural strike key, motion = silhouette heuristic, user = override. */
  source: 'meta' | 'rig' | 'motion' | 'user'
  point_source: 'socket' | 'silhouette' | 'user'
  point: [number, number]
  points: [number, number][]
}

export interface StepHits {
  step_id: string
  frame_w: number
  frame_h: number
  pivot: [number, number]
  clips: string[]
  socket: boolean
  sheet: BlobRef | null
  meta: BlobRef | null
  tracks: HitTrack[]
}

export interface HitVfx {
  step_id: string
  run_id: string
  name: string
  prompt: string
  mode: string | null
  sheet: BlobRef
  meta: BlobRef
  frames: number
  fps: number
  loop: boolean
  frame_w: number
  frame_h: number
  created_at: number | null
}
