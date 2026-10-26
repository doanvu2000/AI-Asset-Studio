import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { ProjectCreateBody, ProjectPatchBody, ProjectRow, ProjectSummary, ProjectWithSummary } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import { api } from './connection'

const ACTIVE_KEY = 'studio.project.active'
const SUMMARY_DEBOUNCE_MS = 2000
const SUMMARY_MAX_WAIT_MS = 5000

function readActive(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY)
  } catch {
    return null
  }
}

export const useProjects = defineStore('projects', () => {
  const map = shallowRef(new Map<string, ProjectRow>())
  // Kept apart from the rows so a count refresh never changes a project object (forms watch those).
  const summaries = shallowRef(new Map<string, ProjectSummary>())
  const loaded = ref(false)
  const loading = ref(false)
  const archivedLoaded = ref(false)
  const error = ref<string | null>(null)
  const activeId = ref<string | null>(readActive())
  let dirty = false
  const staleSummaries = new Set<string>()
  let summaryTimer: ReturnType<typeof setTimeout> | undefined
  let summaryFirstAt = 0

  const all = computed(() => [...map.value.values()].sort((a, b) => lastActivity(b) - lastActivity(a)))
  const list = computed(() => all.value.filter((p) => !p.archived))
  const archived = computed(() => all.value.filter((p) => p.archived))
  const active = computed(() => (activeId.value ? map.value.get(activeId.value) : undefined))

  function lastActivity(p: ProjectRow): number {
    return Math.max(summaries.value.get(p.id)?.last_activity ?? 0, p.updated_at)
  }

  function summaryOf(id: string): ProjectSummary | undefined {
    return summaries.value.get(id)
  }

  function rowOf(p: ProjectWithSummary): ProjectRow {
    const { summary, ...row } = p
    if (summary) summaries.value.set(p.id, summary)
    return row
  }

  function byId(id: string | null | undefined): ProjectRow | undefined {
    return id && id !== NO_PROJECT ? map.value.get(id) : undefined
  }

  function setActive(id: string | null): void {
    activeId.value = id && id !== NO_PROJECT ? id : null
    try {
      if (activeId.value) localStorage.setItem(ACTIVE_KEY, activeId.value)
      else localStorage.removeItem(ACTIVE_KEY)
    } catch {
      /* storage unavailable: the choice lasts for this session only */
    }
  }

  function upsert(p: ProjectRow): void {
    map.value.set(p.id, rowOf(p))
    if (p.archived && activeId.value === p.id) setActive(null)
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(map)
    dirty = false
  }

  async function load(opts: { archived?: boolean } = {}): Promise<void> {
    if (!opts.archived) loading.value = true
    try {
      const rows = await api().projects({ archived: !!opts.archived })
      const next = new Map(map.value)
      for (const [id, p] of next) if (p.archived === !!opts.archived) next.delete(id)
      for (const p of rows) next.set(p.id, rowOf(p))
      map.value = next
      triggerRef(summaries)
      if (opts.archived) archivedLoaded.value = true
      else {
        loaded.value = true
        const a = activeId.value ? next.get(activeId.value) : undefined
        if (activeId.value && (!a || a.archived)) setActive(null)
      }
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
      throw e
    } finally {
      if (!opts.archived) loading.value = false
    }
  }

  async function refresh(id: string): Promise<ProjectRow | undefined> {
    const [row, summary] = await Promise.all([api().getProject(id), api().projectSummary(id)])
    map.value.set(id, rowOf({ ...row, summary }))
    triggerRef(map)
    triggerRef(summaries)
    return map.value.get(id)
  }

  /** Runs/versions of these projects changed; their summary counts are refetched shortly, coalesced. */
  function touch(ids: Iterable<string>): void {
    for (const id of ids) if (map.value.has(id)) staleSummaries.add(id)
    if (!staleSummaries.size) return
    clearTimeout(summaryTimer)
    const now = Date.now()
    if (!summaryFirstAt) summaryFirstAt = now
    // A steady event stream would keep pushing a pure debounce back forever.
    const wait = Math.max(0, Math.min(SUMMARY_DEBOUNCE_MS, summaryFirstAt + SUMMARY_MAX_WAIT_MS - now))
    summaryTimer = setTimeout(() => {
      summaryTimer = undefined
      summaryFirstAt = 0
      const ids = [...staleSummaries]
      staleSummaries.clear()
      for (const id of ids) {
        api()
          .projectSummary(id)
          .then((summary) => {
            if (!map.value.has(id)) return
            summaries.value.set(id, summary)
            triggerRef(summaries)
          })
          .catch(() => undefined)
      }
    }, wait)
  }

  async function create(body: ProjectCreateBody): Promise<ProjectRow> {
    const p = await api().createProject(body)
    upsert(p)
    commit()
    return p
  }

  async function patch(id: string, body: ProjectPatchBody): Promise<ProjectRow> {
    const p = await api().patchProject(id, body)
    upsert(p)
    commit()
    return p
  }

  async function archive(id: string): Promise<ProjectRow> {
    const p = await api().archiveProject(id)
    upsert(p)
    commit()
    if (activeId.value === id) setActive(null)
    return p
  }

  return { map, all, list, archived, active, activeId, loaded, loading, archivedLoaded, error, byId, summaryOf, setActive, upsert, commit, load, refresh, touch, create, patch, archive }
})
