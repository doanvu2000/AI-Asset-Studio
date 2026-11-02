import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { GpuState } from '@/api/types'
import { api } from './connection'

const REFRESH_DEBOUNCE_MS = 150

export const useGpu = defineStore('gpu', () => {
  const state = ref<GpuState | null>(null)
  // Only a mounted GpuPanel needs the state; events with nobody watching must not trigger requests.
  let watchers = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  async function load(): Promise<void> {
    state.value = await api().gpu()
  }

  function refresh(): void {
    if (!watchers) return
    clearTimeout(timer)
    timer = setTimeout(() => void load().catch(() => {}), REFRESH_DEBOUNCE_MS)
  }

  function watch(): () => void {
    watchers += 1
    return () => {
      watchers -= 1
      if (!watchers) clearTimeout(timer)
    }
  }

  return { state, load, refresh, watch }
})
