import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { LogData, PreviewData, ProgressData, RunRow, RunUpdatedData, StepRow } from '@/api/types'
import { RUN_ACTIVE } from '@/api/types'
import { api } from './connection'

export interface LogLine extends LogData {
  ts: number
}

const LOG_CAP = 200

export const useRuns = defineStore('runs', () => {
  const runs = shallowRef(new Map<string, RunRow>())
  const steps = shallowRef(new Map<string, StepRow>())
  const progress = shallowRef(new Map<string, ProgressData>())
  const previews = shallowRef(new Map<string, string>())
  const logs = shallowRef(new Map<string, LogLine[]>())
  const stepIdsByRun = new Map<string, Set<string>>()
  const openDetails = new Set<string>()
  const listLoaded = ref(false)
  const listError = ref<string | null>(null)

  const runList = computed(() => [...runs.value.values()].sort((a, b) => b.created_at - a.created_at))
  const activeRuns = computed(() => runList.value.filter((r) => RUN_ACTIVE.has(r.status)))

  function putStep(s: StepRow): void {
    const prev = steps.value.get(s.id)
    steps.value.set(s.id, s)
    let ids = stepIdsByRun.get(s.run_id)
    if (!ids) stepIdsByRun.set(s.run_id, (ids = new Set()))
    ids.add(s.id)
    if (prev && (prev.attempt !== s.attempt || (prev.status !== 'running' && s.status === 'running'))) {
      progress.value.delete(s.id)
      previews.value.delete(s.id)
    }
  }

  function stepsOf(runId: string): StepRow[] {
    const map = steps.value
    const ids = stepIdsByRun.get(runId)
    if (!ids) return []
    const out: StepRow[] = []
    for (const id of ids) {
      const s = map.get(id)
      if (s) out.push(s)
    }
    return out.sort((a, b) => a.order - b.order)
  }

  function begin() {
    let r = false
    let s = false
    let p = false
    let v = false
    let l = false
    return {
      run(row: RunRow) {
        runs.value.set(row.id, row)
        r = true
      },
      runPatch(id: string, patch: RunUpdatedData) {
        const cur = runs.value.get(id)
        if (!cur) return
        runs.value.set(id, { ...cur, ...patch })
        r = true
      },
      step(row: StepRow) {
        putStep(row)
        s = true
        p = true
        v = true
      },
      progress(id: string, d: ProgressData) {
        progress.value.set(id, d)
        p = true
      },
      preview(id: string, d: PreviewData) {
        previews.value.set(id, `data:${d.mime};base64,${d.b64}`)
        v = true
      },
      log(id: string, d: LogData, ts: number) {
        const arr = logs.value.get(id) ?? []
        arr.push({ ...d, ts })
        if (arr.length > LOG_CAP) arr.splice(0, arr.length - LOG_CAP)
        logs.value.set(id, arr.slice())
        l = true
      },
      commit() {
        if (r) triggerRef(runs)
        if (s) triggerRef(steps)
        if (p) triggerRef(progress)
        if (v) triggerRef(previews)
        if (l) triggerRef(logs)
      }
    }
  }

  async function loadList(): Promise<void> {
    try {
      const rows = await api().listRuns({ limit: 500 })
      for (const row of rows) runs.value.set(row.id, row)
      triggerRef(runs)
      listError.value = null
    } catch (e) {
      listError.value = (e as Error).message
    } finally {
      listLoaded.value = true
    }
  }

  async function loadDetail(id: string): Promise<void> {
    openDetails.add(id)
    const d = await api().getRun(id)
    runs.value.set(d.run.id, d.run)
    for (const s of d.steps) putStep(s)
    triggerRef(runs)
    triggerRef(steps)
  }

  async function reloadDetails(): Promise<void> {
    await Promise.allSettled([...openDetails].map((id) => loadDetail(id)))
  }

  function closeDetail(id: string): void {
    openDetails.delete(id)
  }

  async function cancel(id: string): Promise<void> {
    await api().cancelRun(id)
  }

  async function retry(stepId: string): Promise<void> {
    await api().retryStep(stepId)
  }

  return {
    runs,
    steps,
    progress,
    previews,
    logs,
    runList,
    activeRuns,
    listLoaded,
    listError,
    stepsOf,
    begin,
    loadList,
    loadDetail,
    reloadDetails,
    closeDetail,
    cancel,
    retry
  }
})
