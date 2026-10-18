import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'error'
  text: string
}

export const useToasts = defineStore('toasts', () => {
  const items = ref<Toast[]>([])
  let seq = 0

  function push(text: string, kind: Toast['kind'] = 'info', ms = 4000): void {
    const id = ++seq
    items.value.push({ id, kind, text })
    if (items.value.length > 4) items.value.shift()
    window.setTimeout(() => dismiss(id), ms)
  }

  function dismiss(id: number): void {
    items.value = items.value.filter((t) => t.id !== id)
  }

  function error(e: unknown, prefix = ''): void {
    const msg = e instanceof Error ? e.message : String(e)
    push(prefix ? `${prefix}: ${msg}` : msg, 'error', 6000)
  }

  return { items, push, dismiss, error }
})
