import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { BlobRef, GateAwaitingData, GateResolution, StepRow } from '@/api/types'
import { api, flushEvents } from './connection'
import { useRuns } from './runs'

export interface GateItem {
  stepId: string
  runId: string
  step: StepRow | null
  kind: 'pick' | 'approve'
  k: number
  candidates: BlobRef[]
  since: number
}

function uniqueBlobs(inputs: StepRow['inputs']): BlobRef[] {
  const seen = new Set<string>()
  return Object.values(inputs)
    .flat()
    .filter((b) => !seen.has(b.hash) && !!seen.add(b.hash))
}

// GET /api/gates returns bare StepRows: kind/k live in meta.gate, candidates are the step's inputs.
export function gateFromStep(step: StepRow, prev?: GateItem): GateItem {
  const gate = (step.meta.gate ?? {}) as { kind?: 'pick' | 'approve'; k?: number }
  const kind = prev?.kind ?? gate.kind ?? (step.kind === 'gate.pick' ? 'pick' : 'approve')
  const fallback = uniqueBlobs(step.inputs)
  return {
    stepId: step.id,
    runId: step.run_id,
    step,
    kind,
    k: prev?.k ?? Number(gate.k ?? step.params.k ?? 0),
    candidates: prev?.candidates.length ? prev.candidates : fallback,
    since: step.started_at ?? Date.now() / 1000
  }
}

export const useGates = defineStore('gates', () => {
  const items = shallowRef(new Map<string, GateItem>())
  const loaded = ref(false)
  const error = ref<string | null>(null)
  let dirty = false
  // Live events that land while a snapshot is in flight are newer than it; null marks a gate that went away.
  const inflight = new Set<Map<string, GateItem | null>>()

  function track(id: string): void {
    for (const t of inflight) t.set(id, items.value.get(id) ?? null)
  }

  const list = computed(() => [...items.value.values()].sort((a, b) => a.since - b.since))
  const count = computed(() => items.value.size)

  function onStep(step: StepRow): void {
    if (step.status === 'awaiting_human') items.value.set(step.id, gateFromStep(step, items.value.get(step.id)))
    else if (!items.value.delete(step.id)) {
      track(step.id)
      return
    }
    track(step.id)
    dirty = true
  }

  function onAwaiting(stepId: string, runId: string | null, d: GateAwaitingData): void {
    const prev = items.value.get(stepId)
    items.value.set(stepId, {
      stepId,
      runId: runId ?? prev?.runId ?? '',
      step: prev?.step ?? null,
      kind: d.kind,
      k: d.k ?? 0,
      candidates: d.candidates ?? [],
      since: prev?.since ?? Date.now() / 1000
    })
    track(stepId)
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(items)
    dirty = false
  }

  async function load(): Promise<void> {
    flushEvents()
    const live = new Map<string, GateItem | null>()
    inflight.add(live)
    try {
      const rows = await api().gates()
      const next = new Map<string, GateItem>()
      for (const s of rows) next.set(s.id, gateFromStep(s, items.value.get(s.id)))
      for (const [id, g] of live) {
        if (g) next.set(id, g)
        else next.delete(id)
      }
      items.value = next
      loaded.value = true
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
      throw e
    } finally {
      inflight.delete(live)
    }
  }

  async function resolve(stepId: string, res: GateResolution): Promise<void> {
    const prev = items.value.get(stepId)
    items.value.delete(stepId)
    track(stepId)
    triggerRef(items)
    try {
      await api().resolveGate(stepId, res)
    } catch (e) {
      if (prev) {
        items.value.set(stepId, prev)
        track(stepId)
        triggerRef(items)
      }
      throw e
    }
  }

  /** The step that produced the gate's candidates; retrying it yields a fresh set (new seed). */
  function sourceStep(g: GateItem): StepRow | undefined {
    const step = g.step ?? useRuns().steps.get(g.stepId)
    const deps = step?.deps ?? []
    return useRuns()
      .stepsOf(g.runId)
      .filter((s) => deps.includes(s.key) && !s.kind.startsWith('gate.'))
      .pop()
  }

  async function regenerate(g: GateItem): Promise<void> {
    const src = sourceStep(g)
    if (!src) throw new Error('Không tìm thấy bước sinh ảnh của lượt chạy này')
    await api().retryStep(src.id)
  }

  return { items, loaded, error, list, count, onStep, onAwaiting, commit, load, resolve, sourceStep, regenerate }
})
