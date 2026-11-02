import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import type { FitEntry, FitLevel, FitReport } from '@/api/types'
import { api } from './connection'

export const LEVEL_RANK: Record<FitLevel, number> = { green: 0, yellow: 1, red: 2 }

// Fit is advisory: an old core without /api/fit, or a failed request, must leave the UI exactly as before.
export const useFit = defineStore('fit', () => {
  const report = shallowRef<FitReport | null>(null)
  const loaded = ref(false)
  let inflight: Promise<void> | null = null

  const hardware = computed(() => report.value?.hardware ?? null)

  function load(force = false): Promise<void> {
    if (inflight) return inflight
    if (loaded.value && !force) return Promise.resolve()
    inflight = api()
      .fit()
      .then((r) => {
        report.value = r
      })
      .catch(() => {
        if (!loaded.value) report.value = null
      })
      .finally(() => {
        loaded.value = true
        inflight = null
      })
    return inflight
  }

  const recipe = (id: string): FitEntry | null => report.value?.recipes?.[id] ?? null
  const flow = (id: string): FitEntry | null => report.value?.flows?.[id] ?? null

  /** Worst verdict of several recipes; null when none of them is known. */
  function worst(ids: string[]): FitEntry | null {
    let out: FitEntry | null = null
    for (const id of ids) {
      const e = recipe(id)
      if (e && (!out || LEVEL_RANK[e.level] > LEVEL_RANK[out.level])) out = e
    }
    return out
  }

  return { report, loaded, hardware, load, recipe, flow, worst }
})
