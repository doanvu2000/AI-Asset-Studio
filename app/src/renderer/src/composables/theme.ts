import { ref } from 'vue'
import type { ThemeMode } from '../../../preload/types'

export const THEME_OPTIONS: ReadonlyArray<readonly [ThemeMode, string]> = [
  ['system', 'Theo Windows'],
  ['light', 'Sáng'],
  ['dark', 'Tối']
]

const mode = ref<ThemeMode>('system')
let loaded = false

export function useTheme() {
  const bridge = window.studio
  if (bridge && !loaded) {
    loaded = true
    void bridge.getTheme().then((m) => (mode.value = m))
  }
  async function set(m: ThemeMode): Promise<void> {
    if (!bridge) return
    mode.value = m
    await bridge.setTheme(m)
  }
  return { available: !!bridge, mode, set }
}
