import { defineStore } from 'pinia'
import { computed } from 'vue'
import type { RunMeta, RunRow, StepRow } from '@/api/types'
import { RUN_ACTIVE, STEP_DONE_OK } from '@/api/types'
import { useRuns } from './runs'

export interface Batch {
  id: string
  title: string
  recipeId: string
  createdAt: number
  runs: RunRow[]
  done: number
  failed: number
  active: number
  awaiting: number
  /** status 'running' with a step actually executing. */
  running: number
  /** status 'running' but every live step is queued for a lane (GPU busy with another run). */
  queued: number
}

export function runMeta(r: RunRow): RunMeta {
  const m = r.params?.$meta
  return m && typeof m === 'object' ? (m as RunMeta) : {}
}

export interface StageColumn {
  key: string
  kind: string
  lane: StepRow['lane']
  done: number
  running: number
  total: number
}

export const useBatches = defineStore('batches', () => {
  const runs = useRuns()

  const list = computed<Batch[]>(() => {
    void runs.steps
    const groups = new Map<string, RunRow[]>()
    for (const r of runs.runList) {
      const id = runMeta(r).batch_id
      if (!id) continue
      let g = groups.get(id)
      if (!g) groups.set(id, (g = []))
      g.push(r)
    }
    return [...groups.entries()]
      .map(([id, rs]) => {
        let running = 0
        let queued = 0
        for (const r of rs) {
          if (r.status !== 'running') continue
          const steps = runs.stepsOf(r.id)
          const executing = steps.some((s) => s.status === 'running')
          if (!executing && steps.some((s) => s.status === 'waiting_resource')) queued += 1
          else running += 1
        }
        rs.sort((a, b) => (runMeta(a).batch_index ?? 0) - (runMeta(b).batch_index ?? 0))
        const first = rs[0]!
        return {
          id,
          title: runMeta(first).batch_title || '',
          recipeId: first.recipe_id,
          createdAt: Math.min(...rs.map((r) => r.created_at)),
          runs: rs,
          done: rs.filter((r) => r.status === 'succeeded').length,
          failed: rs.filter((r) => r.status === 'failed' || r.status === 'interrupted' || r.status === 'cancelled').length,
          active: rs.filter((r) => RUN_ACTIVE.has(r.status)).length,
          awaiting: rs.filter((r) => r.status === 'awaiting_human').length,
          running,
          queued
        }
      })
      .sort((a, b) => b.createdAt - a.createdAt)
  })

  function byId(id: string): Batch | undefined {
    return list.value.find((b) => b.id === id)
  }

  // One column per recipe step: how far the whole batch has advanced through that stage.
  function stages(b: Batch): StageColumn[] {
    void runs.steps
    const cols = new Map<string, StageColumn>()
    for (const r of b.runs) {
      for (const s of runs.stepsOf(r.id)) {
        let c = cols.get(s.key)
        if (!c) cols.set(s.key, (c = { key: s.key, kind: s.kind, lane: s.lane, done: 0, running: 0, total: 0 }))
        c.total += 1
        if (STEP_DONE_OK.has(s.status)) c.done += 1
        else if (s.status === 'running' || s.status === 'waiting_resource' || s.status === 'awaiting_human') c.running += 1
      }
    }
    const order = new Map<string, number>()
    for (const r of b.runs) for (const s of runs.stepsOf(r.id)) if (!order.has(s.key)) order.set(s.key, s.order)
    return [...cols.values()].sort((x, y) => (order.get(x.key) ?? 0) - (order.get(y.key) ?? 0))
  }

  return { list, byId, stages }
})
