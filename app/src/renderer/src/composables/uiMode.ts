import { computed } from 'vue'
import { useLocalStorage } from '@vueuse/core'

export type UiMode = 'simple' | 'advanced'

const mode = useLocalStorage<UiMode>('ui-mode', 'simple')

export function useUiMode() {
  const advanced = computed(() => mode.value === 'advanced')
  function toggle(): void {
    mode.value = advanced.value ? 'simple' : 'advanced'
  }
  return { mode, advanced, toggle }
}
