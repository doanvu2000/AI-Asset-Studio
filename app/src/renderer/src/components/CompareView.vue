<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ArrowLeftRight, Check, Columns2, Pause, Play, ScanSearch, SlidersHorizontal, X, Zap } from '@lucide/vue'
import { useElementSize, useEventListener, useIntervalFn } from '@vueuse/core'
import type { BlobRef, QaResult, VersionStatus } from '@/api/types'
import { bytes } from '@/composables/format'
import { useFocusTrap } from '@/composables/focusTrap'
import { useReducedMotion } from '@/composables/motion'
import { blobUrl } from '@/stores/connection'
import AssetViewer from './viewers/AssetViewer.vue'
import QaBadge from './QaBadge.vue'
import { isImage, viewsFor } from './viewers/detect'

export interface CompareItem {
  key: string
  name: string
  meta: string
  /** Without a status the review buttons are hidden (e.g. comparing from the lightbox). */
  status?: VersionStatus
  lead: BlobRef
  siblings: BlobRef[]
  qa?: QaResult | null
}

type Mode = 'slide' | 'side' | 'blink'

const props = defineProps<{ items: CompareItem[] | null }>()
const emit = defineEmits<{ close: []; review: [key: string, action: 'approve' | 'reject'] }>()

const STATUS_TEXT: Record<VersionStatus, string> = { draft: 'Nháp', approved: 'Đã duyệt', rejected: 'Từ chối' }
const MODES: Array<{ id: Mode; label: string; key: string; icon: typeof Columns2 }> = [
  { id: 'slide', label: 'Trượt', key: '1', icon: SlidersHorizontal },
  { id: 'side', label: 'Cạnh nhau', key: '2', icon: Columns2 },
  { id: 'blink', label: 'Nháy', key: '3', icon: Zap }
]
const BLINK_MS = 600
const MIN_ZOOM = 0.2
const MAX_ZOOM = 40

const reduced = useReducedMotion()
const swapped = ref(false)
const pair = computed(() => {
  const list = props.items?.slice(0, 2) ?? []
  return swapped.value ? [...list].reverse() : list
})
const imageMode = computed(() => pair.value.length === 2 && pair.value.every((it) => isImage(it.lead)))
const mode = ref<Mode>('slide')
const eff = computed<Mode>(() => (imageMode.value ? mode.value : 'side'))
const views = computed(() => pair.value.map((it) => viewsFor(it.lead, it.siblings)[0]))

const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const body = ref<HTMLElement | null>(null)
const isOpen = computed(() => pair.value.length === 2)
const trap = useFocusTrap(root, isOpen, closeBtn)

const natural = reactive<Record<string, { w: number; h: number }>>({})
function dims(b: BlobRef): { w: number; h: number } | null {
  if (natural[b.hash]) return natural[b.hash]!
  return b.width && b.height ? { w: b.width, h: b.height } : null
}
function onLoaded(b: BlobRef, e: Event): void {
  const img = e.target as HTMLImageElement
  natural[b.hash] = { w: img.naturalWidth, h: img.naturalHeight }
}
function infoLine(b: BlobRef): string {
  const d = dims(b)
  return [d ? `${d.w}×${d.h}` : '', bytes(b.size)].filter(Boolean).join(' · ')
}

// Both images are laid out at one shared scale so a 512 px and a 1024 px version keep their real size ratio.
const { width: bodyW, height: bodyH } = useElementSize(body)
const paneW = computed(() => (eff.value === 'side' ? bodyW.value / 2 : bodyW.value))
const fit = computed(() => {
  const ds = pair.value.map((it) => dims(it.lead)).filter((d): d is { w: number; h: number } => !!d)
  if (!ds.length || !paneW.value || !bodyH.value) return 1
  const w = Math.max(...ds.map((d) => d.w))
  const h = Math.max(...ds.map((d) => d.h))
  return Math.min(paneW.value / w, bodyH.value / h, 8) * 0.96
})
const zoom = ref(1)
const tx = ref(0)
const ty = ref(0)
const scale = computed(() => fit.value * zoom.value)
const worldStyle = computed(() => ({ transform: `translate(${tx.value}px, ${ty.value}px) scale(${scale.value})` }))
const pixelated = computed(() => scale.value >= 2)
function resetView(): void {
  zoom.value = 1
  tx.value = 0
  ty.value = 0
}

function onWheel(e: WheelEvent): void {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const cx = e.clientX - r.left - r.width / 2
  const cy = e.clientY - r.top - r.height / 2
  const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom.value * Math.exp(-e.deltaY * 0.0016)))
  const k = z / zoom.value
  tx.value = cx - (cx - tx.value) * k
  ty.value = cy - (cy - ty.value) * k
  zoom.value = z
}

const panning = ref(false)
let last = { x: 0, y: 0 }
function panStart(e: PointerEvent): void {
  if (e.button !== 0) return
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  panning.value = true
  last = { x: e.clientX, y: e.clientY }
}
function panMove(e: PointerEvent): void {
  if (!panning.value) return
  tx.value += e.clientX - last.x
  ty.value += e.clientY - last.y
  last = { x: e.clientX, y: e.clientY }
}
function panEnd(): void {
  panning.value = false
}

const pos = ref(50)
const smooth = ref(false)
const dragging = ref(false)
function setPos(v: number, animate = false): void {
  smooth.value = animate && !reduced.value
  pos.value = Math.min(100, Math.max(0, v))
}
function handleDown(e: PointerEvent): void {
  e.stopPropagation()
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  dragging.value = true
}
function handleMove(e: PointerEvent): void {
  if (!dragging.value || !body.value) return
  const r = body.value.getBoundingClientRect()
  setPos(((e.clientX - r.left) / r.width) * 100)
}

const showB = ref(false)
const auto = ref(!reduced.value)
const { pause, resume } = useIntervalFn(() => (showB.value = !showB.value), BLINK_MS, { immediate: false })
const blinking = computed(() => isOpen.value && eff.value === 'blink' && auto.value && !reduced.value)
watch(blinking, (on) => (on ? resume() : pause()), { immediate: true })
function flip(): void {
  auto.value = false
  showB.value = !showB.value
}

watch(isOpen, (on) => {
  if (!on) return
  mode.value = 'slide'
  swapped.value = false
  pos.value = 50
  showB.value = false
  auto.value = !reduced.value
  resetView()
})

const panes = computed<Array<{ id: string; layers: number[] }>>(() =>
  eff.value === 'side'
    ? [
        { id: 'a', layers: [0] },
        { id: 'b', layers: [1] }
      ]
    : [{ id: 'ab', layers: [0, 1] }]
)
function layerStyle(i: number): Record<string, string> {
  if (eff.value === 'slide' && i === 1) return { clipPath: `inset(0 0 0 ${pos.value}%)` }
  if (eff.value === 'blink') return { visibility: (showB.value ? 1 : 0) === i ? 'visible' : 'hidden' }
  return {}
}
const chipA = computed(() => (eff.value === 'blink' ? !showB.value : true))
const chipB = computed(() => (eff.value === 'blink' ? showB.value : true))

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!isOpen.value) return
    if (trap.onTab(e)) return
    const t = e.target as HTMLElement | null
    if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
    const plain = !e.ctrlKey && !e.metaKey && !e.altKey
    if (e.key === 'Escape') emit('close')
    else if (imageMode.value && plain && eff.value === 'slide' && e.key === 'ArrowLeft') setPos(pos.value - 5, true)
    else if (imageMode.value && plain && eff.value === 'slide' && e.key === 'ArrowRight') setPos(pos.value + 5, true)
    else if (imageMode.value && plain && MODES.some((m) => m.key === e.key)) mode.value = MODES.find((m) => m.key === e.key)!.id
    else if (imageMode.value && plain && eff.value === 'blink' && e.key === ' ') flip()
    else if (imageMode.value && plain && e.key === '0') resetView()
    else return
    e.preventDefault()
    e.stopPropagation()
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="cmp">
      <div v-if="isOpen" ref="root" class="scrim" role="dialog" aria-modal="true" aria-label="So sánh 2 bản" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <h2 class="headline">So sánh 2 bản</h2>
            <div v-if="imageMode" class="seg" role="radiogroup" aria-label="Kiểu so sánh">
              <button
                v-for="m in MODES"
                :key="m.id"
                type="button"
                role="radio"
                :aria-checked="mode === m.id"
                :title="`${m.label} (${m.key})`"
                @click="mode = m.id"
              >
                <component :is="m.icon" /> {{ m.label }}
              </button>
            </div>
            <span v-else class="caption">Xem cạnh nhau rồi duyệt từng bản</span>
            <span class="grow" />
            <template v-if="imageMode">
              <button
                v-if="eff === 'blink'"
                type="button"
                class="btn btn-plain"
                :disabled="reduced"
                :aria-pressed="auto"
                :title="reduced ? 'Hệ thống đang tắt chuyển động: đổi A/B bằng phím Space' : auto ? 'Dừng nháy (Space đổi A/B)' : 'Nháy tự động'"
                @click="auto = !auto"
              >
                <Pause v-if="auto" /><Play v-else /> {{ auto ? 'Dừng' : 'Nháy' }}
              </button>
              <button type="button" class="btn btn-plain zoom num" title="Về vừa khít (0, nhấp đúp)" @click="resetView">
                <ScanSearch /> {{ Math.round(scale * 100) }}%
              </button>
            </template>
            <button type="button" class="btn btn-plain" title="Đổi chỗ A / B" @click="swapped = !swapped"><ArrowLeftRight /> Đổi chỗ</button>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div ref="body" class="body" :class="[`m-${eff}`, { img: imageMode, panning }]">
            <template v-if="imageMode">
              <div
                v-for="p in panes"
                :key="p.id"
                class="pane"
                @pointerdown="panStart"
                @pointermove="panMove"
                @pointerup="panEnd"
                @pointercancel="panEnd"
                @wheel.prevent="onWheel"
                @dblclick="resetView"
              >
                <div v-for="i in p.layers" :key="pair[i]!.key" class="layer checker" :style="layerStyle(i)">
                  <div class="world" :style="worldStyle">
                    <img
                      :src="blobUrl(pair[i]!.lead.hash)"
                      :alt="pair[i]!.name"
                      draggable="false"
                      :class="{ px: pixelated }"
                      :style="{ width: `${dims(pair[i]!.lead)?.w ?? 0}px`, height: `${dims(pair[i]!.lead)?.h ?? 0}px`, marginLeft: `${-(dims(pair[i]!.lead)?.w ?? 0) / 2}px`, marginTop: `${-(dims(pair[i]!.lead)?.h ?? 0) / 2}px` }"
                      @load="onLoaded(pair[i]!.lead, $event)"
                    />
                  </div>
                </div>
                <span v-if="p.id === 'a' || p.id === 'ab'" class="chip a" :class="{ off: !chipA }">A</span>
                <span v-if="p.id === 'b' || p.id === 'ab'" class="chip b" :class="{ off: !chipB }">B</span>
              </div>
              <div
                v-if="eff === 'slide'"
                class="handle"
                :class="{ smooth, dragging }"
                :style="{ left: `${pos}%` }"
                role="slider"
                tabindex="0"
                aria-label="Vị trí so sánh"
                aria-valuemin="0"
                aria-valuemax="100"
                :aria-valuenow="Math.round(pos)"
                @pointerdown="handleDown"
                @pointermove="handleMove"
                @pointerup="dragging = false"
                @pointercancel="dragging = false"
              >
                <span class="grip"><ArrowLeftRight :size="14" /></span>
              </div>
            </template>
            <template v-else>
              <div v-for="(it, i) in pair" :key="it.key" class="pane plain" :class="`vs-${it.status ?? 'draft'}`">
                <AssetViewer v-if="views[i]" :view="views[i]!" compact />
              </div>
            </template>
          </div>

          <div class="foot">
            <article v-for="(it, i) in pair" :key="it.key" class="cap" :class="it.status ? `vs-${it.status}` : ''">
              <span v-if="imageMode" class="tag" :class="i ? 'b' : 'a'">{{ i ? 'B' : 'A' }}</span>
              <div class="who">
                <span class="name selectable" :title="it.name">{{ it.name }}</span>
                <span class="caption num meta">{{ [it.meta, infoLine(it.lead)].filter(Boolean).join(' · ') }}</span>
              </div>
              <QaBadge :qa="it.qa" />
              <Transition v-if="it.status" name="fade" mode="out-in">
                <span :key="it.status" class="pill">{{ STATUS_TEXT[it.status] }}</span>
              </Transition>
              <div v-if="it.status" class="review" role="group" :aria-label="`Duyệt ${it.name}`">
                <button type="button" class="btn rv ok" :class="{ on: it.status === 'approved' }" :aria-pressed="it.status === 'approved'" @click="emit('review', it.key, 'approve')">
                  <Check /> Duyệt
                </button>
                <button type="button" class="btn rv no" :class="{ on: it.status === 'rejected' }" :aria-pressed="it.status === 'rejected'" @click="emit('review', it.key, 'reject')">
                  <X /> Từ chối
                </button>
              </div>
            </article>
          </div>
          <p v-if="imageMode" class="hint caption">
            <template v-if="eff === 'slide'">← → di thanh 5%</template>
            <template v-else-if="eff === 'blink'">Space đổi A/B</template>
            <template v-else>Cuộn để thu phóng, kéo để di chuyển, hai khung đi cùng nhau</template>
            · 1 / 2 / 3 đổi kiểu · Esc đóng
          </p>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 32px 24px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.sheet {
  width: min(1480px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto auto;
  border-radius: var(--r-xl);
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px 10px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.grow {
  flex: 1;
}
.zoom {
  min-width: 76px;
}
.body {
  position: relative;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5px;
  height: min(68vh, 720px);
  min-height: 300px;
  background: var(--separator);
  overflow: hidden;
}
.body.m-slide,
.body.m-blink {
  grid-template-columns: minmax(0, 1fr);
}
.pane {
  position: relative;
  overflow: hidden;
  background: var(--bg-sunken);
  touch-action: none;
  cursor: grab;
  user-select: none;
}
.panning .pane {
  cursor: grabbing;
}
.pane.plain {
  cursor: default;
}
.vs-rejected.plain {
  opacity: 0.6;
}
.layer {
  position: absolute;
  inset: 0;
}
.world {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 0;
  height: 0;
  transform-origin: 0 0;
  will-change: transform;
}
.world img {
  position: absolute;
  left: 0;
  top: 0;
  max-width: none;
  pointer-events: none;
}
.world img.px {
  image-rendering: pixelated;
}
.chip,
.tag {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  font-size: var(--fs-caption);
  font-weight: 700;
  color: #fff;
}
.chip {
  position: absolute;
  top: 10px;
  z-index: 2;
  pointer-events: none;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
  transition: opacity var(--t-fast);
}
.chip.a,
.tag.a {
  left: 10px;
  background: var(--accent);
}
.chip.b,
.tag.b {
  right: 10px;
  background: var(--st-ok);
}
.tag {
  flex: none;
}
.m-side .chip.b {
  right: auto;
  left: 10px;
}
.chip.off {
  opacity: 0.35;
}
.handle {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 3;
  width: 28px;
  margin-left: -14px;
  cursor: ew-resize;
  touch-action: none;
}
.handle::before {
  content: '';
  position: absolute;
  left: 13px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #fff;
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.35);
}
.grip {
  position: absolute;
  left: 0;
  top: 50%;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin-top: -14px;
  border-radius: 50%;
  color: var(--text);
  background: #fff;
  box-shadow: var(--shadow-2);
  transition: transform var(--t-fast) var(--ease-spring);
}
.handle:hover .grip,
.handle.dragging .grip {
  transform: scale(1.12);
}
.handle:focus-visible .grip {
  box-shadow: 0 0 0 3px var(--accent);
}
.handle.smooth {
  transition: left var(--t-fast) var(--ease-out);
}
.foot {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-top: 0.5px solid var(--separator);
}
.cap {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 10px 12px 10px 16px;
}
.cap + .cap {
  border-left: 0.5px solid var(--separator);
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pill {
  flex: none;
  font-size: var(--fs-caption);
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  color: var(--text-2);
  background: var(--bg-active);
}
.vs-approved .pill {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.vs-rejected .pill {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.vs-draft .pill {
  color: var(--accent);
  background: var(--accent-soft);
}
.hint {
  margin: 0;
  padding: 6px 16px 8px;
  text-align: center;
  border-top: 0.5px solid var(--separator);
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
.cmp-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.cmp-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.cmp-leave-active {
  transition: opacity var(--t-fast) var(--ease-in);
}
.cmp-enter-from,
.cmp-leave-to {
  opacity: 0;
}
.cmp-enter-from .sheet {
  transform: scale(0.96);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .cmp-enter-active .sheet,
  .handle.smooth,
  .grip {
    transition: none;
  }
  .cmp-enter-from .sheet {
    transform: none;
  }
}
</style>
