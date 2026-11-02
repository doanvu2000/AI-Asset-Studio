import { useLocalStorage } from '@vueuse/core'

export const notifyEnabled = useLocalStorage('notify-enabled', true)
