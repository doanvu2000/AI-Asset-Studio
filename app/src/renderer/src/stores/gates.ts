import { defineStore } from 'pinia'
import { computed, shallowRef, triggerRef } from 'vue'
import type { BlobRef, GateAwaitingData, GateResolution, StepRow } from '@/api/types'
import { api } from './connection'
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
  let dirty = false

  const list = computed(() => [...items.value.values()].sort((a, b) => a.since - b.since))
  const count = computed(() => items.value.size)

  function onStep(step: StepRow): void {
    if (step.status === 'awaiting_human') items.value.set(step.id, gateFromStep(step, items.value.get(step.id)))
    else if (!items.value.delete(step.id)) return
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
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(items)
    dirty = false
  }

  async function load(): Promise<void> {
    const rows = await api().gates()
    const next = new Map<string, GateItem>()
    for (const s of rows) next.set(s.id, gateFromStep(s, items.value.get(s.id)))
    items.value = next
  }

  async function resolve(stepId: string, res: GateResolution): Promise<void> {
    const prev = items.value.get(stepId)
    items.value.delete(stepId)
    triggerRef(items)
    try {
      await api().resolveGate(stepId, res)
    } catch (e) {
      if (prev) {
        items.value.set(stepId, prev)
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

  return { items, list, count, onStep, onAwaiting, commit, load, resolve, sourceStep, regenerate }
})
