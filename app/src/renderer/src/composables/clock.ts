import { onScopeDispose, ref, type Ref } from 'vue'

// Shared clocks run only while at least one component on screen reads them, so N timers cost one interval.
function sharedClock(periodMs: number): () => Ref<number> {
  const now = ref(Date.now() / 1000)
  let users = 0
  let timer: number | undefined
  return () => {
    users += 1
    if (users === 1) {
      now.value = Date.now() / 1000
      timer = window.setInterval(() => (now.value = Date.now() / 1000), periodMs)
    }
    onScopeDispose(() => {
      users -= 1
      if (users === 0) window.clearInterval(timer)
    })
    return now
  }
}

export const useNow = sharedClock(1000)
export const useRelativeNow = sharedClock(30_000)
