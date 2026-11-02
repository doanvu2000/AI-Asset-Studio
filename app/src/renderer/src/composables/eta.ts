import { ref } from 'vue'
import type { RunRow, StepRow, WorkflowNodeStatus, WorkflowStatus } from '@/api/types'
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

type NodeDef = { id: string; recipe_id: string; map?: Record<string, string> }
const OPEN_NODE: ReadonlySet<WorkflowNodeStatus> = new Set(['pending', 'running'])

function nodeDeps(n: NodeDef, ids: ReadonlySet<string>): string[] {
  const out = new Set<string>()
  for (const src of Object.values(n.map ?? {})) {
    const ref = src.split('.')[0] ?? ''
    if (ids.has(ref) && ref !== n.id) out.add(ref)
  }
  return [...out]
}

/**
 * Seconds a workflow run still needs: the longest chain of unfinished nodes by recipe median, a running node
 * counting only what its median leaves after `elapsed`. null when any unfinished node has no median.
 */
export function flowSecondsLeft(
  nodes: readonly NodeDef[],
  status: (nodeId: string) => WorkflowNodeStatus | undefined,
  medians: ReadonlyMap<string, number>,
  elapsed: (nodeId: string) => number
): number | null {
  const ids = new Set(nodes.map((n) => n.id))
  const memo = new Map<string, number>()
  let missing = false
  const finish = (n: NodeDef, trail: ReadonlySet<string>): number => {
    const known = memo.get(n.id)
    if (known !== undefined) return known
    if (trail.has(n.id)) return 0
    const st = status(n.id)
    let own = 0
    if (st && OPEN_NODE.has(st)) {
      const p50 = medians.get(n.recipe_id)
      if (p50 === undefined) missing = true
      else own = st === 'running' ? Math.max(0, p50 - elapsed(n.id)) : p50
    }
    const next = new Set(trail).add(n.id)
    let before = 0
    for (const d of nodeDeps(n, ids)) {
      const dn = nodes.find((x) => x.id === d)
      if (dn) before = Math.max(before, finish(dn, next))
    }
    memo.set(n.id, before + own)
    return before + own
  }
  const total = nodes.reduce((m, n) => Math.max(m, finish(n, new Set())), 0)
  return missing ? null : total
}

/** Batch estimate: runs queue on the same GPU lane, so the remaining node medians of every live run add up. */
export function batchSecondsLeft(
  nodes: readonly NodeDef[],
  runs: readonly { status: WorkflowStatus; nodes: Record<string, WorkflowNodeStatus> }[],
  medians: ReadonlyMap<string, number>
): number | null {
  let total = 0
  let counted = 0
  for (const r of runs) {
    if (r.status !== 'queued' && r.status !== 'running') continue
    for (const n of nodes) {
      const st = r.nodes[n.id] ?? 'pending'
      if (!OPEN_NODE.has(st)) continue
      const p50 = medians.get(n.recipe_id)
      if (p50 === undefined) return null
      total += p50
    }
    counted += 1
  }
  return counted ? total : null
}

export function secondsEta(seconds: number | null): Eta | null {
  return seconds === null ? null : seconds <= NEAR_DONE_S ? { kind: 'soon' } : { kind: 'left', seconds }
}
