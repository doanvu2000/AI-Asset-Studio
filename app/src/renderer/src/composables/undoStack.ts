import { computed, shallowRef } from 'vue'

export const UNDO_LIMIT = 50

export function useUndoStack<T>(limit = UNDO_LIMIT) {
  const past = shallowRef<T[]>([])
  const future = shallowRef<T[]>([])
  const canUndo = computed(() => past.value.length > 0)
  const canRedo = computed(() => future.value.length > 0)

  function push(state: T): void {
    past.value = [...past.value, state].slice(-limit)
    future.value = []
  }
  function undo(current: T): T | undefined {
    const prev = past.value[past.value.length - 1]
    if (prev === undefined) return undefined
    past.value = past.value.slice(0, -1)
    future.value = [...future.value, current].slice(-limit)
    return prev
  }
  function redo(current: T): T | undefined {
    const next = future.value[future.value.length - 1]
    if (next === undefined) return undefined
    future.value = future.value.slice(0, -1)
    past.value = [...past.value, current].slice(-limit)
    return next
  }
  function clear(): void {
    past.value = []
    future.value = []
  }
  return { canUndo, canRedo, push, undo, redo, clear }
}
