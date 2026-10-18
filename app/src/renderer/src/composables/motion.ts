import { computed, type ComputedRef, type MaybeRefOrGetter } from 'vue'
import { TransitionPresets, usePreferredReducedMotion, useTransition } from '@vueuse/core'

const reduced = (() => {
  let r: ComputedRef<boolean> | null = null
  return () => {
    if (!r) {
      const pref = usePreferredReducedMotion()
      r = computed(() => pref.value === 'reduce')
    }
    return r
  }
})()

export function useReducedMotion(): ComputedRef<boolean> {
  return reduced()
}

export function useTweened(source: MaybeRefOrGetter<number>, duration = 280): ComputedRef<number> {
  return useTransition(source, {
    duration,
    transition: TransitionPresets.easeOutCubic,
    disabled: reduced()
  })
}
