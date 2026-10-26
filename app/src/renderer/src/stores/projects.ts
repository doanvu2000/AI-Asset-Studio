import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { ProjectCreateBody, ProjectPatchBody, ProjectRow, ProjectWithSummary } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import { api } from './connection'

const ACTIVE_KEY = 'studio.project.active'
const SUMMARY_DEBOUNCE_MS = 800

function readActive(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY)
  } catch {
    return null
  }
}

export const useProjects = defineStore('projects', () => {
  const map = shallowRef(new Map<string, ProjectWithSummary>())
  const loaded = ref(false)
  const archivedLoaded = ref(false)
  const error = ref<string | null>(null)
  const activeId = ref<string | null>(readActive())
  let dirty = false
  const staleSummaries = new Set<string>()
  let summaryTimer: ReturnType<typeof setTimeout> | undefined

  const all = computed(() => [...map.value.values()].sort((a, b) => lastActivity(b) - lastActivity(a)))
  const list = computed(() => all.value.filter((p) => !p.archived))
  const archived = computed(() => all.value.filter((p) => p.archived))
  const active = computed(() => (activeId.value ? map.value.get(activeId.value) : undefined))

  function lastActivity(p: ProjectWithSummary): number {
    return Math.max(p.summary?.last_activity ?? 0, p.updated_at)
  }

  function byId(id: string | null | undefined): ProjectWithSummary | undefined {
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
    const cur = map.value.get(p.id)
    map.value.set(p.id, { ...p, summary: cur?.summary })
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(map)
    dirty = false
  }

  async function load(opts: { archived?: boolean } = {}): Promise<void> {
    try {
      const rows = await api().projects({ archived: !!opts.archived })
      const next = new Map(map.value)
      for (const [id, p] of next) if (p.archived === !!opts.archived) next.delete(id)
      for (const p of rows) next.set(p.id, p)
      map.value = next
      if (opts.archived) archivedLoaded.value = true
      else loaded.value = true
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
      throw e
    }
  }

  async function refresh(id: string): Promise<ProjectWithSummary | undefined> {
    const [row, summary] = await Promise.all([api().getProject(id), api().projectSummary(id)])
    map.value.set(id, { ...row, summary })
    triggerRef(map)
    return map.value.get(id)
  }

  /** Runs/versions of these projects changed; their summary counts are refetched shortly, coalesced. */
  function touch(ids: Iterable<string>): void {
    for (const id of ids) if (map.value.has(id)) staleSummaries.add(id)
    if (!staleSummaries.size || summaryTimer) return
    summaryTimer = setTimeout(() => {
      summaryTimer = undefined
      const ids = [...staleSummaries]
      staleSummaries.clear()
      for (const id of ids) {
        api()
          .projectSummary(id)
          .then((summary) => {
            const cur = map.value.get(id)
            if (!cur) return
            map.value.set(id, { ...cur, summary })
            triggerRef(map)
          })
          .catch(() => undefined)
      }
    }, SUMMARY_DEBOUNCE_MS)
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

  return { map, all, list, archived, active, activeId, loaded, archivedLoaded, error, byId, setActive, upsert, commit, load, refresh, touch, create, patch, archive }
})
