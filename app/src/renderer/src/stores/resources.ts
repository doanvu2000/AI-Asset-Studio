import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { ResourceHealth } from '@/api/types'
import { api } from './connection'

const ORDER = ['comfyui', 'blender', 'codex', 'claude']

export const useResources = defineStore('resources', () => {
  const map = shallowRef(new Map<string, ResourceHealth>())
  const loaded = ref(false)
  const pending = ref(new Set<string>())
  let dirty = false

  const list = computed(() =>
    [...map.value.values()].sort((a, b) => {
      const ia = ORDER.indexOf(a.name)
      const ib = ORDER.indexOf(b.name)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  )

  function upsert(r: ResourceHealth): void {
    map.value.set(r.name, r)
    dirty = true
  }

  function commit(): void {
    if (dirty) triggerRef(map)
    dirty = false
  }

  async function load(): Promise<void> {
    const rows = await api().resources()
    const next = new Map<string, ResourceHealth>()
    for (const r of rows) next.set(r.name, r)
    map.value = next
    loaded.value = true
  }

  async function toggle(name: string, start: boolean): Promise<void> {
    pending.value.add(name)
    try {
      const r = start ? await api().startResource(name) : await api().stopResource(name)
      upsert(r)
      commit()
    } finally {
      pending.value.delete(name)
    }
  }

  return { map, list, loaded, pending, upsert, commit, load, toggle }
})
