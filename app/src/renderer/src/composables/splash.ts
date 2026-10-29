import { ref } from 'vue'

export const SPLASH_MIN_MS = 3200
export const SPLASH_MAX_MS = 5000

/** True until the launch splash has started fading out; dialogs that pop on launch wait for it. */
export const splashVisible = ref(true)
