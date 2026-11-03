import { api } from '@/stores/connection'
import type { StudioApi } from './client'

export interface RunPreset {
  id: string
  name: string
  recipe_id: string
  project_id: string | null
  inputs: Record<string, unknown>
  params: Record<string, unknown>
  created_at: number
}

export interface RunPresetBody {
  name: string
  recipe_id: string
  project_id?: string | null
  inputs: Record<string, unknown>
  params: Record<string, unknown>
}

export type RunPresetsApi = Pick<StudioApi, 'listRunPresets' | 'saveRunPreset' | 'deleteRunPreset'>

export function runPresetsApi(): RunPresetsApi {
  return api()
}
