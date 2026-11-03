import { computed, shallowRef, type ComputedRef } from 'vue'
import { api } from '@/stores/connection'

export type ScanKind = 'audit' | 'restyle' | 'qa'
export type ScanPhase = 'start' | 'walk' | 'files' | 'analyze' | 'done' | 'cancelled' | 'error'

export interface ScanProgress {
  job: string
  kind: ScanKind
  key: string
  done: number
  total: number
  phase: ScanPhase
}

export const SCAN_EVENT = 'scan.progress'

const live = shallowRef<Partial<Record<ScanKind, ScanProgress>>>({})
const cancelling = shallowRef<Partial<Record<ScanKind, boolean>>>({})

export function recordScan(data: Record<string, unknown>): void {
  const p = data as unknown as ScanProgress
  const next = { ...live.value }
  if (p.phase === 'done' || p.phase === 'cancelled' || p.phase === 'error') {
    if (next[p.kind]?.job !== p.job) return
    delete next[p.kind]
    cancelling.value = { ...cancelling.value, [p.kind]: false }
  } else {
    next[p.kind] = p
  }
  live.value = next
}

export async function cancelScan(kind: ScanKind): Promise<void> {
  cancelling.value = { ...cancelling.value, [kind]: true }
  try {
    await api().cancelScan({ job: live.value[kind]?.job, kind })
  } catch {
    cancelling.value = { ...cancelling.value, [kind]: false }
  }
}

export function useScanProgress(kind: ScanKind): { progress: ComputedRef<ScanProgress | null>; cancelling: ComputedRef<boolean>; cancel: () => Promise<void> } {
  return {
    progress: computed(() => live.value[kind] ?? null),
    cancelling: computed(() => !!cancelling.value[kind]),
    cancel: () => cancelScan(kind)
  }
}
