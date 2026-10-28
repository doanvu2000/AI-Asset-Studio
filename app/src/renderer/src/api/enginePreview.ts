import type { BlobRef } from './types'

export type PreviewEngine = 'godot' | 'unity'

export interface PreviewEngineInfo {
  available: boolean
  path: string
  project?: string
  reason: string | null
}

export interface PreviewEngines {
  engines: Record<PreviewEngine, PreviewEngineInfo>
}

export type PreviewStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface EnginePreviewJob {
  id: string
  step_id: string
  engine: PreviewEngine
  export_dir: string
  status: PreviewStatus
  started_at: number
  ended_at: number | null
  seconds: number
  target: string
  image: BlobRef | null
  errors: string[]
  warnings: string[]
  note: string
  log_tail: string
}

export interface EnginePreviewBody {
  engine: PreviewEngine
  name?: string
  ppu?: number
  pixel?: boolean | null
}
