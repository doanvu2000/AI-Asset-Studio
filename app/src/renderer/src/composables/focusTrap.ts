import { nextTick, watch, type Ref } from 'vue'

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement)
}

/**
 * Modal focus handling: remembers what had focus when `open` turns true, keeps Tab inside `root`, and hands focus
 * back on close so keyboard users land where they were.
 */
export function useFocusTrap(root: Ref<HTMLElement | null>, open: Ref<boolean>, initial?: Ref<HTMLElement | null>): { onTab: (e: KeyboardEvent) => boolean } {
  let restore: HTMLElement | null = null
  watch(
    open,
    (on, was) => {
      if (on && !was) {
        restore = document.activeElement instanceof HTMLElement ? document.activeElement : null
        void nextTick(() => (initial?.value ?? (root.value ? focusables(root.value)[0] : null))?.focus())
      } else if (!on && was) {
        const el = restore
        restore = null
        if (el?.isConnected) void nextTick(() => el.focus({ preventScroll: true }))
      }
    },
    { immediate: true }
  )

  function onTab(e: KeyboardEvent): boolean {
    const r = root.value
    if (e.key !== 'Tab' || !r) return false
    const list = focusables(r)
    if (!list.length) return false
    const first = list[0]!
    const last = list[list.length - 1]!
    const active = document.activeElement as HTMLElement | null
    if (!active || !r.contains(active)) {
      ;(e.shiftKey ? last : first).focus()
    } else if (e.shiftKey && active === first) {
      last.focus()
    } else if (!e.shiftKey && active === last) {
      first.focus()
    } else return false
    e.preventDefault()
    return true
  }

  return { onTab }
}
