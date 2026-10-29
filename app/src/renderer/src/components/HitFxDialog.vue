<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { CircleAlert, LoaderCircle, Minus, Plus, RotateCcw, Swords, X } from '@lucide/vue'
import type { HitTrack, HitVfx, StepHits } from '@/api/hitFx'
import { api, blobUrl } from '@/stores/connection'
import { useFocusTrap } from '@/composables/focusTrap'
import { saveHit, savedHit } from '@/composables/hitFx'
import SpriteSheetViewer from './viewers/SpriteSheetViewer.vue'
import type { HitOverlay } from './viewers/hitOverlay'
import './viewers/viewer.css'

const props = defineProps<{ stepId: string }>()
const open = defineModel<boolean>('open', { required: true })

const root = ref<HTMLElement | null>(null)
const trap = useFocusTrap(root, open)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const error = ref('')
const hits = shallowRef<StepHits | null>(null)
const library = shallowRef<HitVfx[]>([])
const vfxStep = ref('')
const frames = ref<Record<string, number>>({})
const points = ref<Record<string, [number, number]>>({})
const size = ref(0.5)
const viewRow = ref(-1)
const vfxImg = shallowRef<HTMLImageElement | null>(null)

const SOURCE: Record<HitTrack['source'], string> = { meta: 'theo metadata', rig: 'theo rig', motion: 'tự dò', user: 'sửa tay' }
const DIR_NAME: Record<string, string> = { S: 'Nam', SW: 'Tây Nam', W: 'Tây', NW: 'Tây Bắc', N: 'Bắc', NE: 'Đông Bắc', E: 'Đông', SE: 'Đông Nam' }

let token = 0
async function load(): Promise<void> {
  const t = ++token
  state.value = 'loading'
  error.value = ''
  try {
    const [h, lib] = await Promise.all([api().stepHits(props.stepId), api().hitVfxLibrary()])
    if (t !== token) return
    if (!h.tracks.length) throw new Error('Sheet này không có clip tấn công (attack) để gắn hiệu ứng')
    hits.value = h
    library.value = lib.vfx
    const saved = savedHit(props.stepId)
    vfxStep.value = saved && lib.vfx.some((v) => v.step_id === saved.vfx_step) ? saved.vfx_step : (lib.vfx[0]?.step_id ?? '')
    frames.value = { ...(saved?.frames ?? {}) }
    points.value = { ...(saved?.points ?? {}) }
    size.value = saved?.size ?? 0.5
    state.value = 'ready'
  } catch (e) {
    if (t !== token) return
    error.value = (e as Error).message
    state.value = 'error'
  }
}
watch(open, (on) => on && void load(), { immediate: true })

const vfx = computed(() => library.value.find((v) => v.step_id === vfxStep.value) ?? null)
watch(
  vfx,
  (v) => {
    vfxImg.value = null
    if (!v) return
    const el = new Image()
    el.decoding = 'async'
    el.onload = () => {
      if (vfx.value?.step_id === v.step_id) vfxImg.value = el
    }
    el.src = blobUrl(v.sheet.hash)
  },
  { immediate: true }
)

const frameOf = (t: HitTrack): number => frames.value[t.track] ?? t.auto_frame
function pointOf(t: HitTrack): [number, number] {
  return points.value[t.track] ?? t.points[frameOf(t)] ?? t.point
}
const edited = (t: HitTrack): boolean => t.track in frames.value || t.track in points.value

function step(t: HitTrack, d: number): void {
  const f = Math.min(t.frames - 1, Math.max(0, frameOf(t) + d))
  const next = { ...frames.value }
  if (f === t.auto_frame) delete next[t.track]
  else next[t.track] = f
  frames.value = next
  const p = { ...points.value }
  delete p[t.track]
  points.value = p
}
function reset(t: HitTrack): void {
  const f = { ...frames.value }
  const p = { ...points.value }
  delete f[t.track]
  delete p[t.track]
  frames.value = f
  points.value = p
}

const overlay = computed<HitOverlay | null>(() => {
  const h = hits.value
  const v = vfx.value
  const im = vfxImg.value
  if (!h || !v || !im) return null
  const rows: HitOverlay['rows'] = {}
  for (const t of h.tracks) {
    const [x, y] = pointOf(t)
    rows[t.row] = { frame: frameOf(t), x, y }
  }
  const cols = Math.max(1, Math.round((v.sheet.width ?? im.naturalWidth) / v.frame_w))
  return { rows, image: im, cellW: v.frame_w, cellH: v.frame_h, cols, count: v.frames, fps: v.fps, size: size.value, clip: h.tracks[0]?.clip }
})

function onPoint(p: { row: number; x: number; y: number }): void {
  const t = hits.value?.tracks.find((x) => x.row === p.row)
  if (t) points.value = { ...points.value, [t.track]: [p.x, p.y] }
}

function thumbStyle(v: HitVfx): Record<string, string> {
  const cols = Math.max(1, Math.round((v.sheet.width ?? v.frame_w) / v.frame_w))
  const rows = Math.max(1, Math.round((v.sheet.height ?? v.frame_h) / v.frame_h))
  // The fifth cell: a one-shot burst is still tiny on its first frames.
  const k = Math.min(v.frames - 1, 4)
  return {
    backgroundImage: `url("${blobUrl(v.sheet.hash)}")`,
    backgroundSize: `${cols * 100}% ${rows * 100}%`,
    backgroundPosition: `${cols > 1 ? ((k % cols) / (cols - 1)) * 100 : 0}% ${rows > 1 ? (Math.floor(k / cols) / (rows - 1)) * 100 : 0}%`
  }
}

const bound = computed(() => !!savedHit(props.stepId)?.enabled)
function save(): void {
  const v = vfx.value
  if (!v) return
  saveHit(props.stepId, { enabled: true, vfx_step: v.step_id, vfx_name: v.name, frames: { ...frames.value }, points: { ...points.value }, size: size.value })
  open.value = false
}
function unbind(): void {
  saveHit(props.stepId, null)
  open.value = false
}

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!open.value) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    open.value = false
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="hf">
      <div v-if="open" class="scrim" @mousedown.self="open = false">
        <div ref="root" class="sheet" role="dialog" aria-modal="true" aria-labelledby="hf-title">
          <header class="head">
            <Swords :size="16" class="h-ic" aria-hidden="true" />
            <div class="h-text">
              <h2 id="hf-title" class="title-2">Hiệu ứng trúng đòn</h2>
              <p class="caption">Gắn VFX vào khung chém của clip tấn công — xuất kèm cho Unity (AnimationEvent) và Godot</p>
            </div>
            <button type="button" class="btn btn-plain btn-icon" title="Đóng" aria-label="Đóng" @click="open = false"><X :size="16" aria-hidden="true" /></button>
          </header>

          <Transition name="hf-swap" mode="out-in">
            <div v-if="state === 'loading'" key="l" class="state" role="status"><LoaderCircle :size="20" class="spin" /> Đang dò khung trúng đòn…</div>
            <div v-else-if="state === 'error'" key="e" class="state err" role="alert">
              <CircleAlert :size="20" />
              <span>{{ error }}</span>
              <button type="button" class="btn" @click="load"><RotateCcw /> Thử lại</button>
            </div>
            <div v-else-if="hits" key="r" class="body">
              <div class="preview">
                <SpriteSheetViewer
                  v-if="hits.sheet && hits.meta"
                  :sheet="hits.sheet"
                  :meta="hits.meta"
                  :hit-fx="overlay"
                  compact
                  @view="viewRow = $event.row"
                  @point="onPoint"
                />
              </div>
              <aside class="side">
                <section>
                  <h3 class="sec">Hiệu ứng</h3>
                  <p v-if="!library.length" class="caption muted">Thư viện chưa có VFX flipbook — chạy công thức “Hiệu ứng VFX flipbook” trước.</p>
                  <div v-else class="vfx-list" role="radiogroup" aria-label="Chọn VFX">
                    <button
                      v-for="v in library"
                      :key="v.step_id"
                      type="button"
                      role="radio"
                      class="vfx"
                      :class="{ on: v.step_id === vfxStep }"
                      :aria-checked="v.step_id === vfxStep"
                      :title="v.prompt || v.name"
                      @click="vfxStep = v.step_id"
                    >
                      <span class="vfx-th checker" :style="thumbStyle(v)" />
                      <span class="vfx-name">{{ v.name }}</span>
                      <span class="caption num">{{ v.frames }} khung</span>
                    </button>
                  </div>
                  <label class="size">
                    <span class="caption">Cỡ VFX</span>
                    <input v-model.number="size" class="vw-range" type="range" min="0.2" max="1.5" step="0.05" aria-label="Cỡ VFX so với chiều cao nhân vật" />
                    <span class="num caption">{{ Math.round(size * 100) }}%</span>
                  </label>
                </section>
                <section>
                  <h3 class="sec">Khung trúng đòn</h3>
                  <p class="caption muted">Bấm vào ảnh để đặt lại điểm trúng của hướng đang xem.</p>
                  <ul class="tracks">
                    <li v-for="t in hits.tracks" :key="t.track" class="trk" :class="{ cur: t.row === viewRow }">
                      <span class="dir">{{ DIR_NAME[t.dir] ?? t.dir }}</span>
                      <span class="src caption">{{ edited(t) ? SOURCE.user : SOURCE[t.source] }}</span>
                      <div class="stepper">
                        <button type="button" class="btn btn-plain btn-icon" :disabled="frameOf(t) <= 0" :aria-label="`Khung trước — ${t.track}`" @click="step(t, -1)"><Minus :size="13" /></button>
                        <span class="num fr">{{ frameOf(t) + 1 }}/{{ t.frames }}</span>
                        <button type="button" class="btn btn-plain btn-icon" :disabled="frameOf(t) >= t.frames - 1" :aria-label="`Khung sau — ${t.track}`" @click="step(t, 1)"><Plus :size="13" /></button>
                      </div>
                      <button type="button" class="btn btn-plain btn-icon reset" :class="{ hide: !edited(t) }" :disabled="!edited(t)" title="Về giá trị tự dò" :aria-label="`Đặt lại ${t.track}`" @click="reset(t)">
                        <RotateCcw :size="13" />
                      </button>
                    </li>
                  </ul>
                </section>
              </aside>
            </div>
          </Transition>

          <footer class="foot">
            <button v-if="bound" type="button" class="btn btn-plain" @click="unbind">Bỏ gắn</button>
            <span class="grow" />
            <button type="button" class="btn btn-plain" @click="open = false">Huỷ</button>
            <button type="button" class="btn btn-primary" :disabled="state !== 'ready' || !vfx" @click="save">Gắn khi xuất</button>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 6vh 16px 16px;
  background: var(--scrim);
}
.sheet {
  width: min(940px, 100%);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: var(--r-xl);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 12px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.h-ic {
  flex: none;
  color: var(--accent);
}
.h-text {
  flex: 1;
  min-width: 0;
}
.h-text > * {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 320px;
  color: var(--text-2);
}
.state.err {
  color: var(--danger);
}
.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
}
.preview {
  min-height: 460px;
  display: flex;
}
.preview > * {
  flex: 1;
}
.side {
  display: grid;
  align-content: start;
  gap: 16px;
  padding: 12px 14px;
  overflow: auto;
  border-left: 0.5px solid var(--separator);
}
.sec {
  margin: 0 0 6px;
  font-size: var(--fs-callout);
  font-weight: 600;
}
.muted {
  color: var(--text-3);
  margin: 0 0 6px;
}
.vfx-list {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.vfx {
  display: grid;
  justify-items: center;
  gap: 3px;
  padding: 5px 4px;
  border: 0;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  color: var(--text-2);
  font: inherit;
  transition:
    background-color var(--t-fast),
    box-shadow var(--t-fast);
}
.vfx.on {
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1.5px var(--accent);
  color: var(--text);
}
.vfx-th {
  width: 56px;
  height: 56px;
  border-radius: 4px;
  background-repeat: no-repeat;
}
.vfx-name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-caption);
}
.size {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.size input {
  flex: 1;
}
.tracks {
  display: grid;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.trk {
  display: grid;
  grid-template-columns: 72px 1fr auto auto;
  align-items: center;
  gap: 6px;
  padding: 2px 4px 2px 8px;
  border-radius: var(--r-sm);
  transition: background-color var(--t-fast);
}
.trk.cur {
  background: var(--accent-soft);
}
.dir {
  font-size: var(--fs-callout);
}
.src {
  color: var(--text-3);
}
.stepper {
  display: inline-flex;
  align-items: center;
}
.fr {
  min-width: 34px;
  text-align: center;
  font-size: var(--fs-callout);
}
.reset {
  transition: opacity var(--t-fast);
}
.reset.hide {
  opacity: 0;
}
.foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-top: 0.5px solid var(--separator);
}
.grow {
  flex: 1;
}
.spin {
  animation: spin 1s linear infinite;
}
.hf-enter-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.hf-enter-active .sheet {
  transition: transform var(--t-base) var(--ease-spring);
}
.hf-leave-active {
  transition: opacity 120ms var(--ease-in);
}
.hf-enter-from,
.hf-leave-to {
  opacity: 0;
}
.hf-enter-from .sheet {
  transform: translateY(-8px) scale(0.98);
}
.hf-swap-enter-active,
.hf-swap-leave-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.hf-swap-enter-from,
.hf-swap-leave-to {
  opacity: 0;
}
@media (max-width: 760px) {
  .body {
    grid-template-columns: 1fr;
  }
  .side {
    border-left: 0;
    border-top: 0.5px solid var(--separator);
  }
}
@media (prefers-reduced-motion: reduce) {
  .hf-enter-active,
  .hf-enter-active .sheet,
  .hf-leave-active,
  .hf-swap-enter-active,
  .hf-swap-leave-active,
  .vfx,
  .trk,
  .reset {
    transition: none;
  }
}
</style>
