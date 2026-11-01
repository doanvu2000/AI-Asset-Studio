import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { WorkflowDef, WorkflowRunDetail, WorkflowRunRow } from '@/api/types'
import { api } from './connection'

// Child-run events arrive many per second; the detail (progress, previews) is re-read at most this often.
const REFRESH_MS = 600

export const useFlows = defineStore('flows', () => {
  const defs = shallowRef<WorkflowDef[]>([])
  const defsLoaded = ref(false)
  const defsError = ref<string | null>(null)
  const runs = shallowRef(new Map<string, WorkflowRunRow>())
  const runsLoaded = ref(false)
  const runsError = ref<string | null>(null)
  const details = shallowRef(new Map<string, WorkflowRunDetail>())
  const missing = shallowRef(new Set<string>())
  const open = new Set<string>()
  const childToRun = new Map<string, string>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()

  const runList = computed(() => [...runs.value.values()].sort((a, b) => b.created_at - a.created_at))

  function byId(id: string): WorkflowDef | undefined {
    return defs.value.find((d) => d.id === id)
  }

  async function loadDefs(): Promise<void> {
    try {
      defs.value = await api().flows()
      defsError.value = null
    } catch (e) {
      defsError.value = (e as Error).message
    } finally {
      defsLoaded.value = true
    }
  }

  async function loadRuns(): Promise<void> {
    try {
      for (const r of await api().flowRuns({ limit: 100 })) runs.value.set(r.id, r)
      triggerRef(runs)
      runsError.value = null
    } catch (e) {
      runsError.value = (e as Error).message
    } finally {
      runsLoaded.value = true
    }
  }

  function putDetail(d: WorkflowRunDetail): void {
    details.value.set(d.id, d)
    runs.value.set(d.id, d)
    for (const c of d.children) if (c.run_id) childToRun.set(c.run_id, d.id)
    triggerRef(details)
    triggerRef(runs)
  }

  async function fetchDetail(id: string): Promise<void> {
    try {
      putDetail(await api().flowRun(id))
      if (missing.value.delete(id)) triggerRef(missing)
    } catch (e) {
      if ((e as { status?: number }).status === 404) {
        missing.value.add(id)
        details.value.delete(id)
        triggerRef(missing)
        triggerRef(details)
        return
      }
      throw e
    }
  }

  async function loadDetail(id: string): Promise<void> {
    open.add(id)
    await fetchDetail(id)
  }

  function closeDetail(id: string): void {
    open.delete(id)
    clearTimeout(timers.get(id))
    timers.delete(id)
  }

  function scheduleRefresh(id: string): void {
    if (!open.has(id) || timers.has(id)) return
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id)
        void fetchDetail(id).catch(() => undefined)
      }, REFRESH_MS)
    )
  }

  // The row already says which node moved, so the map recolours at once; progress and previews follow with the refetch.
  function onRun(row: WorkflowRunRow): void {
    runs.value.set(row.id, row)
    triggerRef(runs)
    const cur = details.value.get(row.id)
    if (cur) {
      const children = cur.children.map((c) => {
        const s = row.nodes[c.node_id]
        return s ? { ...c, status: s.status, run_id: s.run_id, error: s.error, attempts: s.attempts } : c
      })
      putDetail({ ...cur, ...row, children })
    }
    scheduleRefresh(row.id)
  }

  function onChildEvent(runId: string | null): void {
    const wr = runId ? childToRun.get(runId) : undefined
    if (wr) scheduleRefresh(wr)
  }

  async function start(id: string, body: { inputs: Record<string, unknown>; project_id?: string; title?: string }): Promise<WorkflowRunRow> {
    const row = await api().startFlowRun(id, body)
    runs.value.set(row.id, row)
    triggerRef(runs)
    return row
  }

  async function retryNode(runId: string, nodeId: string): Promise<void> {
    onRun(await api().retryFlowNode(runId, nodeId))
  }

  async function cancel(runId: string): Promise<void> {
    onRun(await api().cancelFlowRun(runId))
  }

  async function resync(): Promise<void> {
    await Promise.allSettled([loadDefs(), loadRuns(), ...[...open].map((id) => fetchDetail(id))])
  }

  return { defs, defsLoaded, defsError, runs, runList, runsLoaded, runsError, details, missing, byId, loadDefs, loadRuns, loadDetail, closeDetail, onRun, onChildEvent, start, retryNode, cancel, resync }
})
