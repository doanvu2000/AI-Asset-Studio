import type { RunRow } from '@/api/types'
import { paletteOpen, shortcutsOpen } from '@/composables/palette'

/** True when a gate shortcut must not fire: typing, an overlay is open, or Enter/Space belongs to a focused control. */
export function isTypingTarget(e: KeyboardEvent): boolean {
  if (paletteOpen.value || shortcutsOpen.value || e.ctrlKey || e.metaKey || e.altKey) return true
  const t = e.target as HTMLElement | null
  if (!t) return false
  if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable) return true
  return (e.key === 'Enter' || e.key === ' ') && !!t.closest('button,a,[role=button],[role=option],[role=menuitem]')
}

const PROMPT_KEYS = ['prompt', 'description', 'brief', 'text']

export function sourcePrompt(run: RunRow | undefined): string {
  if (!run) return ''
  for (const k of PROMPT_KEYS) {
    const v = run.inputs[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return ''
}
