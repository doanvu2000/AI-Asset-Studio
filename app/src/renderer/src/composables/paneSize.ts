import { ref, watch, type Ref } from 'vue'

const PREFIX = 'pane:'

function read(id: string): number | null {
  try {
    const v = Number(localStorage.getItem(PREFIX + id))
    return Number.isFinite(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

function write(id: string, value: number): void {
  try {
    localStorage.setItem(PREFIX + id, String(Math.round(value)))
  } catch {
    /* storage blocked: width just won't persist */
  }
}

/** Width of a resizable pane, persisted per id. */
export function usePaneWidth(id: string, initial: number): Ref<number> {
  const width = ref(read(id) ?? initial)
  let timer: ReturnType<typeof setTimeout> | undefined
  watch(width, (v) => {
    clearTimeout(timer)
    timer = setTimeout(() => write(id, v), 200)
  })
  return width
}
