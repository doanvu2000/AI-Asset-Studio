import { ref } from 'vue'
import { useToasts } from '@/stores/toasts'

const busy = ref(false)

export function useCoreRestart() {
  const toasts = useToasts()

  async function run(): Promise<void> {
    const bridge = window.studio
    if (!bridge || busy.value) return
    busy.value = true
    try {
      await bridge.restartCore()
    } catch (e) {
      toasts.error(e, 'Không khởi động lại được core')
    } finally {
      busy.value = false
    }
  }

  return { available: !!window.studio, busy, run }
}
