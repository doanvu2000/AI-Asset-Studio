import { onScopeDispose, ref, type Ref } from 'vue'

const now = ref(Date.now() / 1000)
let users = 0
let timer: number | undefined

// One shared 1 Hz clock for every elapsed timer on screen, started only while something displays one.
export function useNow(): Ref<number> {
  users += 1
  if (users === 1) {
    now.value = Date.now() / 1000
    timer = window.setInterval(() => (now.value = Date.now() / 1000), 1000)
  }
  onScopeDispose(() => {
    users -= 1
    if (users === 0) window.clearInterval(timer)
  })
  return now
}
