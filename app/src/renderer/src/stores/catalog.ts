import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import type { ExportPreset, KindInfo } from '@/api/types'
import { api } from './connection'

export const useKinds = defineStore('kinds', () => {
  const list = shallowRef<KindInfo[]>([])
  const loaded = ref(false)
  const error = ref<string | null>(null)
  const byKind = computed(() => new Map(list.value.map((k) => [k.kind, k])))

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    try {
      list.value = await api().kinds()
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loaded.value = true
    }
  }

  return { list, loaded, error, byKind, load }
})

export const usePresets = defineStore('presets', () => {
  const list = shallowRef<ExportPreset[]>([])
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    try {
      list.value = await api().presets()
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loaded.value = true
    }
  }

  async function save(p: ExportPreset): Promise<ExportPreset> {
    const saved = await api().savePreset(p)
    list.value = [...list.value.filter((x) => x.id !== saved.id), saved]
    return saved
  }

  async function remove(id: string): Promise<void> {
    await api().deletePreset(id)
    list.value = list.value.filter((x) => x.id !== id)
  }

  return { list, loaded, error, load, save, remove }
})
