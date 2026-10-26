import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef, type Ref } from 'vue'
import type { LogData, PreviewData, ProgressData, RunRow, RunUpdatedData, StepRow } from '@/api/types'
import { RUN_ACTIVE, STEP_TERMINAL } from '@/api/types'
import { api } from './connection'

export interface LogLine extends LogData {
  ts: number
}

const LOG_CAP = 200
// Logs stay readable after a step ends, but only for the most recent steps so long sessions don't grow without bound.
const LOG_STEPS_MAX = 150

function firstImage(s: StepRow): string | null {
  for (const blobs of Object.values(s.outputs ?? {})) {
    const img = blobs.find((b) => b.mime.startsWith('image/'))
    if (img) return img.hash
  }
  return null
}

export const useRuns = defineStore('runs', () => {
  const runs = shallowRef(new Map<string, RunRow>())
  const stepStore = new Map<string, StepRow>()
  const steps = shallowRef(stepStore)
  // Per-run revision so a step event re-renders only views of that run instead of every stepsOf() caller.
  const runRevs = new Map<string, Ref<number>>()
  const touchedRuns = new Set<string>()
  const progress = shallowRef(new Map<string, ProgressData>())
  const previews = shallowRef(new Map<string, string>())
  const logs = shallowRef(new Map<string, LogLine[]>())
  const stepIdsByRun = new Map<string, Set<string>>()
  // Dashboard and run detail can hold the same run; it leaves the resync set only when every holder closes it.
  const openDetails = new Map<string, Set<string>>()
  // Latest image output per run, learned from step rows; wins over the list's preview_hash once known.
  const outputImages = shallowRef(new Map<string, { order: number; hash: string }>())
  const listLoaded = ref(false)
  const listError = ref<string | null>(null)

  const runList = computed(() => [...runs.value.values()].sort((a, b) => b.created_at - a.created_at))
  const activeRuns = computed(() => runList.value.filter((r) => RUN_ACTIVE.has(r.status)))
  // run.status 'running' covers steps queued for a lane too; a run parked at a gate is not running.
  const runningRuns = computed(() => runList.value.filter((r) => r.status === 'running'))
  const awaitingRuns = computed(() => runList.value.filter((r) => r.status === 'awaiting_human'))

  function putStep(s: StepRow): void {
    const prev = stepStore.get(s.id)
    stepStore.set(s.id, s)
    touchedRuns.add(s.run_id)
    let ids = stepIdsByRun.get(s.run_id)
    if (!ids) stepIdsByRun.set(s.run_id, (ids = new Set()))
    ids.add(s.id)
    const img = firstImage(s)
    if (img) {
      const cur = outputImages.value.get(s.run_id)
      if (!cur || cur.order <= s.order) outputImages.value.set(s.run_id, { order: s.order, hash: img })
    }
    if (STEP_TERMINAL.has(s.status) || (prev && (prev.attempt !== s.attempt || (prev.status !== 'running' && s.status === 'running')))) {
      progress.value.delete(s.id)
      previews.value.delete(s.id)
    }
  }

  function revOf(runId: string): Ref<number> {
    let r = runRevs.get(runId)
    if (!r) runRevs.set(runId, (r = ref(0)))
    return r
  }

  function bumpTouched(): void {
    for (const id of touchedRuns) revOf(id).value++
    touchedRuns.clear()
  }

  function stepsOf(runId: string): StepRow[] {
    void revOf(runId).value
    const map = stepStore
    const ids = stepIdsByRun.get(runId)
    if (!ids) return []
    const out: StepRow[] = []
    for (const id of ids) {
      const s = map.get(id)
      if (s) out.push(s)
    }
    return out.sort((a, b) => a.order - b.order)
  }

  function previewHash(runId: string): string | null {
    return outputImages.value.get(runId)?.hash ?? runs.value.get(runId)?.preview_hash ?? null
  }

  /** Live sampler preview of the run's running step, if the core is streaming one. */
  function livePreview(runId: string): string | null {
    const ids = stepIdsByRun.get(runId)
    if (!ids) return null
    for (const id of ids) {
      const src = previews.value.get(id)
      if (src && steps.value.get(id)?.status === 'running') return src
    }
    return null
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
        logs.value.delete(id)
        logs.value.set(id, arr.slice())
        while (logs.value.size > LOG_STEPS_MAX) logs.value.delete(logs.value.keys().next().value!)
        l = true
      },
      commit() {
        if (r) triggerRef(runs)
        if (s) {
          triggerRef(steps)
          triggerRef(outputImages)
          bumpTouched()
        }
        if (p) triggerRef(progress)
        if (v) triggerRef(previews)
        if (l) triggerRef(logs)
      }
    }
  }

  async function loadList(): Promise<void> {
    try {
      // Batches are grouped from loaded runs, so batch runs older than the recent window are fetched separately.
      const [recent, batched] = await Promise.all([
        api().listRuns({ limit: 500 }),
        api().listRuns({ batched: true, limit: 2000 })
      ])
      for (const row of [...batched, ...recent]) runs.value.set(row.id, row)
      triggerRef(runs)
      listError.value = null
    } catch (e) {
      listError.value = (e as Error).message
    } finally {
      listLoaded.value = true
    }
  }

  async function loadBatch(batchId: string): Promise<void> {
    const rows = await api().listRuns({ batch_id: batchId, limit: 2000 })
    for (const row of rows) runs.value.set(row.id, row)
    triggerRef(runs)
  }

  // Live events keep a loaded project's runs current, so reopening its page within a minute skips the 2000-row fetch.
  const projectFetched = new Map<string, number>()
  async function loadProject(projectId: string): Promise<void> {
    if (Date.now() - (projectFetched.get(projectId) ?? 0) < 60_000) return
    const rows = await api().listRuns({ project_id: projectId, limit: 2000 })
    projectFetched.set(projectId, Date.now())
    for (const row of rows) runs.value.set(row.id, row)
    triggerRef(runs)
  }

  async function fetchDetail(id: string): Promise<void> {
    const d = await api().getRun(id)
    runs.value.set(d.run.id, d.run)
    for (const s of d.steps) putStep(s)
    triggerRef(runs)
    triggerRef(steps)
    triggerRef(outputImages)
    triggerRef(progress)
    triggerRef(previews)
    bumpTouched()
  }

  async function loadDetail(id: string, holder = 'detail'): Promise<void> {
    let holders = openDetails.get(id)
    if (!holders) openDetails.set(id, (holders = new Set()))
    holders.add(holder)
    await fetchDetail(id)
  }

  async function reloadDetails(): Promise<void> {
    await Promise.allSettled([...openDetails.keys()].map((id) => fetchDetail(id)))
  }

  function closeDetail(id: string, holder = 'detail'): void {
    const holders = openDetails.get(id)
    if (!holders) return
    holders.delete(holder)
    if (!holders.size) openDetails.delete(id)
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
    runningRuns,
    awaitingRuns,
    previewHash,
    livePreview,
    listLoaded,
    listError,
    stepsOf,
    begin,
    loadList,
    loadBatch,
    loadProject,
    loadDetail,
    reloadDetails,
    closeDetail,
    cancel,
    retry
  }
})
