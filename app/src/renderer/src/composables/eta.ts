import { ref } from 'vue'
import type { RunRow, StepRow } from '@/api/types'
import { api, useConnection } from '@/stores/connection'

// Fewer finished runs say little about how long a recipe takes.
const MIN_SAMPLES = 2
const REFRESH_S = 300
const NEAR_DONE_S = 20

const medians = ref(new Map<string, number>())
let loadedAt = 0
let inflight = false

/** Median active seconds per recipe, shared by every chip and refreshed at most every few minutes. */
export function useRecipeMedians() {
  if (!inflight && useConnection().transport && Date.now() / 1000 - loadedAt > REFRESH_S) {
    inflight = true
    api()
      .statsOverview({})
      .then((res) => {
        const next = new Map<string, number>()
        for (const r of res.recipes) {
          const p50 = r.duration_s.p50
          if (r.succeeded >= MIN_SAMPLES && p50 !== null && p50 > 0) next.set(r.recipe_id, p50)
        }
        medians.value = next
        loadedAt = Date.now() / 1000
      })
      .catch(() => {
        loadedAt = Date.now() / 1000 - REFRESH_S + 30
      })
      .finally(() => (inflight = false))
  }
  return medians
}

/** Seconds the run has actually worked: union of step intervals, so gate waits and parallel overlap don't count. */
export function activeSeconds(run: Pick<RunRow, 'created_at'>, steps: readonly StepRow[] | undefined, now: number): number {
  const spans = (steps ?? [])
    .filter((s) => s.lane !== 'none' && s.started_at !== null && s.status !== 'awaiting_human' && s.status !== 'waiting_resource')
    .map((s) => [s.started_at as number, Math.min(now, s.ended_at ?? now)] as const)
    .filter(([a, b]) => b > a)
    .sort((x, y) => x[0] - y[0])
  if (!steps?.length) return Math.max(0, now - run.created_at)
  let total = 0
  let reach = -Infinity
  for (const [a, b] of spans) {
    total += b - Math.max(a, reach)
    reach = Math.max(reach, b)
  }
  return total
}

export type Eta = { kind: 'left'; seconds: number } | { kind: 'soon' }

/** null = no estimate (no history, or the run is parked at a gate); never negative. */
export function estimateEta(median: number | undefined, activeS: number): Eta | null {
  if (median === undefined) return null
  const left = median - activeS
  return left <= NEAR_DONE_S ? { kind: 'soon' } : { kind: 'left', seconds: left }
}

export function etaLabel(eta: Eta): string {
  if (eta.kind === 'soon') return 'sắp xong'
  const min = Math.ceil(eta.seconds / 60)
  return min < 60 ? `còn ~${min} phút` : `còn ~${Math.floor(min / 60)} giờ ${min % 60 ? `${min % 60} phút` : ''}`.trim()
}
