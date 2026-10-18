<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: number
    defaultSize: number
    min: number
    max: number
    /** `end`: the pane sits before the handle (left pane); `start`: after it (right pane). */
    side?: 'end' | 'start'
    label: string
    collapsedSize?: number
    collapseBelow?: number
    step?: number
  }>(),
  { side: 'end', collapsedSize: undefined, collapseBelow: undefined, step: 16 }
)
const emit = defineEmits<{ 'update:modelValue': [value: number] }>()

const el = ref<HTMLElement | null>(null)
const dragging = ref(false)
const dir = computed(() => (props.side === 'end' ? 1 : -1))
const collapsible = computed(() => props.collapsedSize !== undefined && props.collapseBelow !== undefined)
const collapsed = computed(() => collapsible.value && props.modelValue <= props.collapsedSize!)

let startX = 0
let startW = 0
let lastX = 0
let raf = 0
let animTimer: ReturnType<typeof setTimeout> | undefined

function pane(): HTMLElement | null {
  const h = el.value
  return ((props.side === 'end' ? h?.previousElementSibling : h?.nextElementSibling) as HTMLElement | null) ?? null
}

function clamp(raw: number): number {
  if (collapsible.value && raw < props.collapseBelow!) return props.collapsedSize!
  return Math.round(Math.min(props.max, Math.max(props.min, raw)))
}

// Width is a layout property, so only discrete jumps (reset, snap to rail) get a short transition; live drag follows the pointer 1:1.
function set(value: number, animate = false): void {
  if (value === props.modelValue) return
  const p = pane()
  if (animate && p) {
    clearTimeout(animTimer)
    p.style.transition = 'width var(--t-base) var(--ease-out)'
    animTimer = setTimeout(() => (p.style.transition = ''), 280)
  }
  emit('update:modelValue', value)
}

function onPointerDown(e: PointerEvent): void {
  if (e.button !== 0 || !el.value) return
  e.preventDefault()
  el.value.setPointerCapture(e.pointerId)
  startX = lastX = e.clientX
  startW = pane()?.getBoundingClientRect().width ?? props.modelValue
  dragging.value = true
  pane()?.style.setProperty('will-change', 'width')
  document.documentElement.classList.add('pane-resizing')
}

function onPointerMove(e: PointerEvent): void {
  if (!dragging.value) return
  lastX = e.clientX
  if (raf) return
  raf = requestAnimationFrame(() => {
    raf = 0
    const next = clamp(startW + (lastX - startX) * dir.value)
    const crossed = collapsible.value && (next === props.collapsedSize) !== collapsed.value
    set(next, crossed)
  })
}

function endDrag(e: PointerEvent): void {
  if (!dragging.value) return
  dragging.value = false
  if (el.value?.hasPointerCapture(e.pointerId)) el.value.releasePointerCapture(e.pointerId)
  pane()?.style.removeProperty('will-change')
  document.documentElement.classList.remove('pane-resizing')
}

function onKey(e: KeyboardEvent): void {
  const step = e.shiftKey ? props.step * 4 : props.step
  let next: number | null = null
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    const delta = (e.key === 'ArrowRight' ? 1 : -1) * dir.value * step
    if (collapsed.value) next = delta > 0 ? props.min : props.modelValue
    else {
      const raw = props.modelValue + delta
      next = raw < props.min ? (collapsible.value ? props.collapsedSize! : props.min) : Math.min(props.max, raw)
    }
  } else if (e.key === 'Home') next = collapsible.value ? props.collapsedSize! : props.min
  else if (e.key === 'End') next = props.max
  else if (e.key === 'Enter') next = props.defaultSize
  if (next === null) return
  e.preventDefault()
  set(next, !e.key.startsWith('Arrow') || (collapsible.value && (next === props.collapsedSize) !== collapsed.value))
}

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  clearTimeout(animTimer)
  document.documentElement.classList.remove('pane-resizing')
})
</script>

<template>
  <div
    ref="el"
    class="resize-handle"
    :class="{ dragging }"
    :data-side="side"
    role="separator"
    aria-orientation="vertical"
    tabindex="0"
    :aria-label="label"
    :aria-valuenow="Math.round(modelValue)"
    :aria-valuemin="collapsible ? collapsedSize : min"
    :aria-valuemax="max"
    :title="`${label} — kéo để đổi độ rộng, bấm đúp để về mặc định`"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="endDrag"
    @pointercancel="endDrag"
    @lostpointercapture="endDrag"
    @dblclick="set(defaultSize, true)"
    @keydown="onKey"
  />
</template>

<style scoped>
.resize-handle {
  -webkit-app-region: no-drag;
  position: relative;
  flex: none;
  width: 0;
  z-index: 6;
  touch-action: none;
}
.resize-handle::before {
  content: '';
  position: absolute;
  inset: 0 -5px;
  cursor: col-resize;
}
.resize-handle::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -1px;
  width: 2px;
  border-radius: 1px;
  background: var(--accent);
  opacity: 0;
  transform: scaleX(0.5);
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-fast) var(--ease-out);
  pointer-events: none;
}
.resize-handle:hover::after {
  opacity: 0.5;
  transform: none;
  transition-delay: 120ms;
}
.resize-handle.dragging::after,
.resize-handle:focus-visible::after {
  opacity: 1;
  transform: none;
  transition-delay: 0ms;
}
.resize-handle:focus-visible {
  outline: none;
}
</style>

<style>
:root.pane-resizing,
:root.pane-resizing * {
  cursor: col-resize !important;
}
:root.pane-resizing iframe,
:root.pane-resizing canvas {
  pointer-events: none;
}
</style>
