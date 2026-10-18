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

export interface SharedOrigin {
  rect: DOMRect
  src: string | null
  at: number
}

const origins = new Map<string, SharedOrigin>()

export function rememberOrigin(key: string, el: Element | null | undefined): void {
  if (!el) return
  const img = el.querySelector('img')
  origins.set(key, { rect: el.getBoundingClientRect(), src: img?.currentSrc || img?.src || null, at: performance.now() })
}

// Stale origins are dropped: a run that loads slowly would otherwise fly in from a row that is long gone.
export function takeOrigin(key: string, maxAgeMs = 1200): SharedOrigin | null {
  const o = origins.get(key)
  origins.delete(key)
  return o && performance.now() - o.at <= maxAgeMs ? o : null
}

const EASE = 'cubic-bezier(0.2, 0.9, 0.25, 1)'

/** Flies a ghost of the remembered thumbnail onto `target` (contain-fit square), then reveals the target. */
export function flyFromOrigin(o: SharedOrigin, target: HTMLElement, duration = 360): void {
  const t = target.getBoundingClientRect()
  if (!o.src || !t.width || !o.rect.width) return
  const side = Math.min(t.width, t.height)
  const to = { left: t.left + (t.width - side) / 2, top: t.top + (t.height - side) / 2 }
  const ghost = document.createElement('img')
  ghost.src = o.src
  ghost.alt = ''
  Object.assign(ghost.style, {
    position: 'fixed', left: `${to.left}px`, top: `${to.top}px`, width: `${side}px`, height: `${side}px`,
    objectFit: 'contain', pointerEvents: 'none', zIndex: '60', transformOrigin: '0 0'
  })
  document.body.appendChild(ghost)
  const s = o.rect.width / side
  ghost.animate(
    [
      { transform: `translate(${o.rect.left - to.left}px, ${o.rect.top - to.top}px) scale(${s})`, borderRadius: `${8 / s}px` },
      { transform: 'none', borderRadius: '0' }
    ],
    { duration, easing: EASE }
  ).finished.then(() => ghost.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140 }).finished)
    .finally(() => ghost.remove())
  target.animate([{ opacity: 0 }, { opacity: 0, offset: 0.7 }, { opacity: 1 }], { duration: duration + 140 })
}

export function useTweened(source: MaybeRefOrGetter<number>, duration = 280): ComputedRef<number> {
  return useTransition(source, {
    duration,
    transition: TransitionPresets.easeOutCubic,
    disabled: reduced()
  })
}
