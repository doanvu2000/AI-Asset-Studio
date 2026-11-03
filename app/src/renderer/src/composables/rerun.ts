import { api } from '@/stores/connection'

export type RerunSeed = 'new' | 'same'

export function rerunRun(id: string, seed: RerunSeed): Promise<{ run_id: string }> {
  return api().rerunRun(id, seed)
}
