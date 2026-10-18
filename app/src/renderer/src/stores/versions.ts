import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { VersionRow } from '@/api/types'
import { api } from './connection'

export const useVersions = defineStore('versions', () => {
  const map = shallowRef(new Map<string, VersionRow>())
  const loaded = ref(false)
  let dirty = false

  const list = computed(() => [...map.value.values()].sort((a, b) => b.created_at - a.created_at))
  const draftCount = computed(() => list.value.filter((v) => v.status === 'draft').length)

  function upsert(v: VersionRow): void {
    map.value.set(v.id, v)
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(map)
    dirty = false
  }

  async function load(): Promise<void> {
    const rows = await api().versions()
    const next = new Map<string, VersionRow>()
    for (const v of rows) next.set(v.id, v)
    map.value = next
    loaded.value = true
  }

  async function setStatus(id: string, action: 'approve' | 'reject'): Promise<void> {
    const prev = map.value.get(id)
    if (!prev) return
    upsert({ ...prev, status: action === 'approve' ? 'approved' : 'rejected' })
    commit()
    try {
      upsert(await api().setVersionStatus(id, action))
    } catch (e) {
      upsert(prev)
      throw e
    } finally {
      commit()
    }
  }

  return { map, list, draftCount, loaded, upsert, commit, load, setStatus }
})
