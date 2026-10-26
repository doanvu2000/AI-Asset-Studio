import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

const sources = ref(new Set<symbol>())
let reported: boolean | null = null

function report(): void {
  const dirty = sources.value.size > 0
  if (dirty === reported) return
  reported = dirty
  window.studio?.setDirty?.(dirty)
}

/** Tells the main process about unsaved edits so closing the window asks first. */
export function useUnsavedChanges(dirty: Ref<boolean>): void {
  const id = Symbol('unsaved')
  watch(
    dirty,
    (d) => {
      const next = new Set(sources.value)
      if (d) next.add(id)
      else next.delete(id)
      sources.value = next
      report()
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    if (!sources.value.has(id)) return
    const next = new Set(sources.value)
    next.delete(id)
    sources.value = next
    report()
  })
}
