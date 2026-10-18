import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import type { VersionRow } from '@/api/types'
import { api } from './connection'

// Generator output names like "1074.png", "00012_.png" or a bare hash say nothing about the asset.
const MEANINGLESS = /^(?:[\d_\-\s]+|[0-9a-f]{12,})$/i

function splitExt(name: string): [string, string] {
  const i = name.lastIndexOf('.')
  return i > 0 ? [name.slice(0, i), name.slice(i)] : [name, '']
}

/**
 * Name for an exported copy. Meaningful original names are kept as-is because Spine atlases and sheet JSON
 * reference sibling files by name; only opaque generator names are replaced with the asset name.
 */
export function exportName(v: Pick<VersionRow, 'asset_name' | 'blob'>): string | undefined {
  const orig = v.blob.name ?? ''
  const [stem, ext] = splitExt(orig)
  if (orig && !MEANINGLESS.test(stem)) return orig
  const asset = v.asset_name.trim()
  return asset ? `${asset}${ext}` : orig || undefined
}

export const useVersions = defineStore('versions', () => {
  const map = shallowRef(new Map<string, VersionRow>())
  const loaded = ref(false)
  let dirty = false

  const list = computed(() => [...map.value.values()].sort((a, b) => b.created_at - a.created_at))
  const draftCount = computed(() => list.value.filter((v) => v.status === 'draft').length)
  const byHash = computed(() => {
    const m = new Map<string, VersionRow>()
    for (const v of list.value) if (!m.has(v.blob.hash)) m.set(v.blob.hash, v)
    return m
  })

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

  /** One asset can span several versions (Spine skeleton + atlas + texture); they are reviewed together. */
  async function setStatusMany(ids: string[], action: 'approve' | 'reject'): Promise<void> {
    const results = await Promise.allSettled(ids.map((id) => setStatus(id, action)))
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (failed) throw failed.reason
  }

  return { map, list, byHash, draftCount, loaded, upsert, commit, load, setStatus, setStatusMany }
})
