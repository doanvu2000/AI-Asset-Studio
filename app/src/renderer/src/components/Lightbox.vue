<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, ChevronLeft, ChevronRight, X } from '@lucide/vue'
import { useEventListener } from '@vueuse/core'
import type { BlobRef, VersionStatus } from '@/api/types'
import { bytes } from '@/composables/format'
import { useReducedMotion } from '@/composables/motion'
import { useFocusTrap } from '@/composables/focusTrap'
import BlobImage from './BlobImage.vue'
import AssetActions from './AssetActions.vue'
import AssetViewer from './viewers/AssetViewer.vue'
import { VIEW_LABEL, viewsFor } from './viewers/detect'

const props = withDefaults(
  defineProps<{
    blob: BlobRef | null
    siblings?: BlobRef[]
    /** Asset name shown instead of the raw file name. */
    title?: string
    /** One line under the title, e.g. "Prop 3D · 5 phút trước". */
    meta?: string
    runId?: string
    versionId?: string
    filename?: string
    /** Set to show Duyệt / Từ chối; the parent applies the change. */
    status?: VersionStatus
    /** Parent-driven navigation (e.g. across gallery assets); without it the arrows step through `siblings`. */
    index?: number
    count?: number
    /** Screen rect of the thumbnail that opened it; the sheet grows out of it. */
    origin?: DOMRect | null
  }>(),
  { siblings: () => [] }
)
const emit = defineEmits<{ close: []; prev: []; next: []; review: [action: 'approve' | 'reject', advance: boolean] }>()
const closeBtn = ref<HTMLButtonElement | null>(null)
const scrim = ref<HTMLElement | null>(null)
const isOpen = computed(() => !!props.blob)
const trap = useFocusTrap(scrim, isOpen, closeBtn)

const localHash = ref<string | null>(null)
const external = computed(() => props.count !== undefined)
const shown = computed<BlobRef | null>(() => {
  if (!props.blob) return null
  if (external.value || !localHash.value) return props.blob
  return props.siblings.find((b) => b.hash === localHash.value) ?? props.blob
})
const ring = computed(() => (props.siblings.some((b) => b.hash === props.blob?.hash) ? props.siblings : []))
const pos = computed(() => {
  if (external.value) return { i: props.index ?? 0, n: props.count ?? 0 }
  return { i: Math.max(0, ring.value.findIndex((b) => b.hash === shown.value?.hash)), n: ring.value.length }
})
const canNav = computed(() => pos.value.n > 1)

function step(d: -1 | 1): void {
  const { i, n } = pos.value
  if (n < 2) return
  if (external.value) {
    if (i + d < 0 || i + d >= n) return
    if (d < 0) emit('prev')
    else emit('next')
    return
  }
  localHash.value = ring.value[(i + d + n) % n]!.hash
}
const atStart = computed(() => external.value && pos.value.i <= 0)
const atEnd = computed(() => external.value && pos.value.i >= pos.value.n - 1)

const views = computed(() => (shown.value ? viewsFor(shown.value, props.siblings) : []))
const mode = ref(0)
const view = computed(() => views.value[mode.value] ?? views.value[0])
// A plain picture keeps the compact square sheet; real viewers get a wide stage with room for controls.
const plain = computed(() => views.value.length === 1 && view.value?.kind === 'image')
const heading = computed(() => props.title || shown.value?.name || shown.value?.hash.slice(0, 12) || '')
const fileLine = computed(() => {
  const b = shown.value
  if (!b) return ''
  const parts = [props.title && b.name ? b.name : '', b.width ? `${b.width}×${b.height}` : '', bytes(b.size)]
  return parts.filter(Boolean).join(' · ')
})

// Capture phase on document runs before views' own window-level shortcuts (Gates uses ←/→ to move between candidates).
useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!props.blob) return
    const t = e.target as HTMLElement | null
    // Teleported popups (export BundleMenu) handle their own keys, Escape included.
    if (t?.closest('[role="menu"], [data-popup]')) return
    if (trap.onTab(e)) return
    if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
    // A focused viewer (sprite sheet) owns only its playback keys; A/R/Esc keep working after clicking it.
    if (t?.closest('[data-own-keys]') && (e.key.startsWith('Arrow') || e.key === ' ' || e.key === 'n' || e.key === 'N')) return
    const plainKey = !e.ctrlKey && !e.metaKey && !e.altKey
    if (e.key === 'Escape') emit('close')
    else if (e.key === 'ArrowLeft' && canNav.value) step(-1)
    else if (e.key === 'ArrowRight' && canNav.value) step(1)
    else if (plainKey && props.status && (e.key === 'a' || e.key === 'A')) emit('review', 'approve', true)
    else if (plainKey && props.status && (e.key === 'r' || e.key === 'R')) emit('review', 'reject', true)
    else return
    e.preventDefault()
    e.stopPropagation()
  },
  { capture: true }
)

watch(
  () => props.blob?.hash,
  () => {
    localHash.value = null
    mode.value = 0
  }
)
watch(localHash, () => (mode.value = 0))

const reduced = useReducedMotion()
function growFromOrigin(el: Element): void {
  const o = props.origin
  const sheet = (el as HTMLElement).querySelector<HTMLElement>('.sheet')
  if (!o || !sheet || reduced.value || !o.width) return
  // The rect still carries the CSS enter scale (centered), so recover the untransformed box from offset sizes.
  const r = sheet.getBoundingClientRect()
  const w = sheet.offsetWidth
  if (!w) return
  const left = r.left + (r.width - w) / 2
  const top = r.top + (r.height - sheet.offsetHeight) / 2
  const s = o.width / w
  sheet.animate(
    [
      { transformOrigin: '0 0', transform: `translate(${o.left - left}px, ${o.top - top}px) scale(${s})`, borderRadius: `${8 / s}px` },
      { transformOrigin: '0 0', transform: 'none' }
    ],
    { duration: 340, easing: 'cubic-bezier(0.2, 0.9, 0.25, 1)' }
  )
}
</script>

<template>
  <Teleport to="body">
    <Transition name="lb" @enter="growFromOrigin">
      <div v-if="shown && view" ref="scrim" class="scrim" role="dialog" aria-modal="true" :aria-label="heading" @click.self="emit('close')">
        <button v-if="canNav" type="button" class="nav prev" :disabled="atStart" aria-label="Trước (←)" title="Trước (←)" @click="step(-1)">
          <ChevronLeft :size="22" />
        </button>
        <figure class="sheet" :class="{ wide: !plain }">
          <BlobImage v-if="plain" :key="shown.hash" :hash="shown.hash" full class="img" />
          <div v-else class="stage">
            <Transition name="lb-swap" mode="out-in">
              <AssetViewer :key="`${view.kind}:${shown.hash}`" :view="view" />
            </Transition>
          </div>
          <figcaption class="cap">
            <div class="row1">
              <div class="who">
                <span class="name selectable">{{ heading }}</span>
                <span class="caption num meta">
                  <template v-if="meta">{{ meta }} · </template>{{ fileLine }}
                  <template v-if="canNav"> · {{ pos.i + 1 }}/{{ pos.n }}</template>
                </span>
              </div>
              <div v-if="views.length > 1" class="seg" role="radiogroup" aria-label="Kiểu xem">
                <button
                  v-for="(v, i) in views"
                  :key="v.kind"
                  type="button"
                  role="radio"
                  :aria-checked="mode === i"
                  :class="{ on: mode === i }"
                  @click="mode = i"
                >
                  {{ VIEW_LABEL[v.kind] }}
                </button>
              </div>
              <div v-if="status" class="review" role="group" aria-label="Duyệt">
                <button type="button" class="btn rv ok" :class="{ on: status === 'approved' }" :aria-pressed="status === 'approved'" title="Duyệt (A)" @click="emit('review', 'approve', false)">
                  <Check />
                  {{ status === 'approved' ? 'Đã duyệt' : 'Duyệt' }}
                </button>
                <button type="button" class="btn rv no" :class="{ on: status === 'rejected' }" :aria-pressed="status === 'rejected'" title="Từ chối (R)" @click="emit('review', 'reject', false)">
                  <X />
                  {{ status === 'rejected' ? 'Đã từ chối' : 'Từ chối' }}
                </button>
              </div>
              <button ref="closeBtn" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
            </div>
            <AssetActions class="row2" :hash="shown.hash" :filename="filename" :run-id="runId" :version-id="versionId" />
          </figcaption>
        </figure>
        <button v-if="canNav" type="button" class="nav next" :disabled="atEnd" aria-label="Sau (→)" title="Sau (→)" @click="step(1)">
          <ChevronRight :size="22" />
        </button>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) 56px;
  align-items: center;
  justify-items: center;
  gap: 8px;
  padding: 40px 16px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.sheet {
  grid-column: 2;
  margin: 0;
  display: grid;
  gap: 0;
  width: min(74vh, 100%);
  max-height: 100%;
  border-radius: var(--r-xl);
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.sheet.wide {
  width: min(1180px, 100%);
}
.img {
  width: 100%;
  min-height: 0;
  border-radius: 0;
}
.stage {
  height: min(70vh, 760px);
  min-height: 320px;
  background: var(--bg-sunken);
}
.cap {
  display: grid;
  gap: 6px;
  padding: 10px 10px 10px 16px;
  border-top: 0.5px solid var(--separator);
}
.row1 {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
}
.who {
  flex: 1 1 180px;
  min-width: 0;
  display: grid;
}
.name {
  font-size: var(--fs-headline);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row2 {
  margin-left: -6px;
}
.review {
  display: flex;
  gap: 6px;
}
.rv {
  height: 28px;
  padding: 0 10px;
  font-size: var(--fs-callout);
}
.rv.ok.on {
  color: var(--st-ok);
  background: var(--st-ok-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--st-ok) 40%, transparent);
}
.rv.no {
  color: var(--st-fail);
}
.rv.no.on {
  background: var(--st-fail-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--st-fail) 40%, transparent);
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-callout);
  font-weight: 550;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    color var(--t-fast);
}
.seg button.on {
  color: var(--text);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
}
.nav {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: 50%;
  color: #fff;
  background: rgba(255, 255, 255, 0.14);
  box-shadow: inset 0 0 0 0.5px rgba(255, 255, 255, 0.25);
  backdrop-filter: blur(12px);
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    transform var(--t-fast) var(--ease-spring),
    opacity var(--t-fast);
}
.nav:hover {
  background: rgba(255, 255, 255, 0.24);
}
.nav:active {
  transform: scale(0.92);
}
.nav:disabled {
  opacity: 0.3;
  cursor: default;
  transform: none;
}
.nav.prev {
  grid-column: 1;
  grid-row: 1;
}
.nav.next {
  grid-column: 3;
  grid-row: 1;
}
.lb-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.lb-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.lb-leave-active {
  transition: opacity var(--t-fast) var(--ease-in);
}
.lb-enter-from,
.lb-leave-to {
  opacity: 0;
}
.lb-enter-from .sheet {
  transform: scale(0.94);
}
.lb-swap-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.lb-swap-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.lb-swap-enter-from,
.lb-swap-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .lb-enter-active .sheet {
    transition: none;
  }
  .lb-enter-from .sheet {
    transform: none;
  }
}
</style>
