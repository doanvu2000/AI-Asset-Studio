<script setup lang="ts">
import { computed, nextTick, onMounted, ref, shallowRef, watch } from 'vue'
import { useElementSize, useRafFn } from '@vueuse/core'
import { ArrowDown, CircleAlert, Crosshair, LoaderCircle, Pause, Play, RotateCcw, Scan, SquareDashed, ZoomIn, ZoomOut } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import { useReducedMotion } from '@/composables/motion'
import { blobJson } from './fetchBlob'
import { angleText, arrowRotation, directionName, normAngle } from './directions'

interface NamedFrame {
  label?: string
  angle?: number
  index: number
  x: number
  y: number
  w: number
  h: number
}
interface ClipMeta {
  name: string
  row0: number
  rows: number
  frames: number
  fps?: number
  loop?: boolean
}
interface SheetMeta {
  frame_w: number
  frame_h: number
  cols: number
  rows?: number
  count?: number
  padding?: number
  order?: Array<number | string>
  pivot?: [number, number]
  dirs?: string[]
  frames_named?: NamedFrame[]
  preset?: string
  row_angles?: Array<number | string>
  angles?: Array<number | string>
  clips?: ClipMeta[]
}
interface Frame {
  x: number
  y: number
  label: string
  sub: string
  angle: number | null
}

const props = withDefaults(defineProps<{ sheet: BlobRef; meta: BlobRef; normal?: BlobRef | null; compact?: boolean }>(), {
  compact: false,
  normal: null
})

const CLIP_NAME: Record<string, string> = { walk: 'Đi bộ', idle: 'Đứng thở', run: 'Chạy', attack: 'Tấn công', hurt: 'Bị đánh', jump: 'Nhảy' }
const reduced = useReducedMotion()

const state = ref<'loading' | 'ready' | 'error'>('loading')
const error = ref('')
const meta = shallowRef<SheetMeta | null>(null)
const img = shallowRef<HTMLImageElement | null>(null)

function loadImage(hash: string, what: string): Promise<HTMLImageElement> {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const el = new Image()
    el.decoding = 'async'
    el.onload = () => resolve(el)
    el.onerror = () => reject(new Error(`Không tải được ${what}`))
    el.src = blobUrl(hash)
  })
}

// Switching assets quickly can resolve an older load last; only the newest one may write state.
let loadToken = 0
async function load(): Promise<void> {
  const token = ++loadToken
  state.value = 'loading'
  error.value = ''
  try {
    const [m, im] = await Promise.all([blobJson<SheetMeta>(props.meta.hash), loadImage(props.sheet.hash, 'ảnh sheet')])
    if (token !== loadToken) return
    if (!(m.frame_w > 0 && m.frame_h > 0 && m.cols > 0)) throw new Error('JSON sheet thiếu frame_w / frame_h / cols')
    meta.value = m
    img.value = im
    clipIdx.value = 0
    frame.value = 0
    row.value = clips.value[0]?.row0 ?? 0
    if (clips.value[0]?.fps) fps.value = clampFps(clips.value[0].fps)
    zoom.value = 'fit'
    pixel.value = Math.max(m.frame_w, m.frame_h) <= 160
    state.value = 'ready'
  } catch (e) {
    if (token !== loadToken) return
    error.value = (e as Error).message
    state.value = 'error'
  }
}
onMounted(load)
watch(() => [props.sheet.hash, props.meta.hash], load)

const showNormal = ref(false)
const normalImg = shallowRef<HTMLImageElement | null>(null)
const normalState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let normalToken = 0
watch(
  () => props.normal?.hash,
  () => {
    normalToken++
    normalImg.value = null
    normalState.value = 'idle'
    showNormal.value = false
  }
)
watch(showNormal, async (on) => {
  const n = props.normal
  if (!on || !n || normalImg.value || normalState.value === 'loading') return
  const token = ++normalToken
  normalState.value = 'loading'
  try {
    const im = await loadImage(n.hash, 'normal map')
    if (token !== normalToken) return
    normalImg.value = im
    normalState.value = 'ready'
  } catch {
    if (token !== normalToken) return
    normalState.value = 'error'
    showNormal.value = false
  }
})
const source = computed(() => (showNormal.value && normalImg.value ? normalImg.value : img.value))

const total = computed(() => {
  const m = meta.value
  return m ? (m.count ?? m.cols * (m.rows ?? 1)) : 0
})

function rectOf(i: number): { x: number; y: number } {
  const m = meta.value!
  const named = m.frames_named?.find((f) => f.index === i)
  if (named) return { x: named.x, y: named.y }
  const pad = m.padding ?? 0
  return { x: (i % m.cols) * (m.frame_w + pad), y: Math.floor(i / m.cols) * (m.frame_h + pad) }
}

// A sheet is directional when it carries preset labels or one distinct angle per frame; otherwise rows are
// directions/animations and the columns of a row play as frames.
const directional = computed(() => {
  const m = meta.value
  if (!m) return false
  if (m.dirs?.length === total.value) return true
  const angles = (m.angles?.length === total.value ? m.angles : (m.order ?? [])).map(normAngle)
  return (m.rows ?? 1) === 1 && angles.length === total.value && angles.every((a) => a !== null) && new Set(angles).size === angles.length
})

const rowCount = computed(() => (meta.value ? Math.ceil(total.value / meta.value.cols) : 0))
const row = ref(0)

const clips = computed<ClipMeta[]>(() => {
  const list = meta.value?.clips
  if (!Array.isArray(list) || directional.value) return []
  return list.filter(
    (c) => !!c && Number.isInteger(c.row0) && c.row0 >= 0 && c.rows > 0 && c.frames > 0 && c.row0 + c.rows <= rowCount.value
  )
})
const clipIdx = ref(0)
const activeClip = computed<ClipMeta | null>(() => clips.value[clipIdx.value] ?? null)
const clipLabel = (c: ClipMeta): string => CLIP_NAME[c.name] ?? c.name
const shownRows = computed<number[]>(() => {
  const c = activeClip.value
  const [from, n] = c ? [c.row0, c.rows] : [0, rowCount.value]
  return Array.from({ length: n }, (_, i) => from + i)
})

function rowAngle(r: number): number | null {
  const m = meta.value
  if (!m) return null
  const ra = m.row_angles
  if (Array.isArray(ra) && ra.length === rowCount.value) return normAngle(ra[r])
  const c = clips.value.find((x) => r >= x.row0 && r < x.row0 + x.rows)
  if (c) return c.rows === 8 ? (r - c.row0) * 45 : null
  return (m.rows ?? rowCount.value) === 8 && rowCount.value === 8 ? r * 45 : null
}
function rowLabel(r: number): string {
  const a = rowAngle(r)
  return a !== null ? directionName(a) : `Hàng ${r + 1}`
}
function rowTitle(r: number): string {
  const a = rowAngle(r)
  return a !== null ? `${directionName(a)} · ${angleText(a)}` : `Hàng ${r + 1}`
}

const frames = computed<Frame[]>(() => {
  const m = meta.value
  if (!m) return []
  if (directional.value) {
    return Array.from({ length: total.value }, (_, i) => {
      const a = normAngle(m.angles?.[i] ?? m.frames_named?.[i]?.angle ?? m.order?.[i])
      const label = a !== null ? directionName(a) : (m.dirs?.[i] ?? m.frames_named?.[i]?.label ?? `#${i + 1}`)
      return { ...rectOf(i), label, sub: a !== null ? angleText(a) : '', angle: a }
    })
  }
  const start = row.value * m.cols
  const n = activeClip.value ? Math.min(activeClip.value.frames, m.cols) : Math.min(m.cols, total.value - start)
  const a = rowAngle(row.value)
  return Array.from({ length: Math.max(0, n) }, (_, c) => ({ ...rectOf(start + c), label: `${c + 1}`, sub: '', angle: a }))
})

const frame = ref(0)
const fps = ref(8)
const clampFps = (v: number): number => Math.min(24, Math.max(1, Math.round(v)))
const playing = ref(!reduced.value)
const pivot = ref(true)
const checker = ref(true)
const pixel = ref(true)
const zoom = ref<'fit' | number>('fit')

let acc = 0
const raf = useRafFn(
  ({ delta }) => {
    acc += delta
    const step = 1000 / fps.value
    if (acc < step) return
    const n = frames.value.length
    if (n) frame.value = (frame.value + Math.floor(acc / step)) % n
    acc %= step
  },
  { immediate: false }
)
watch(
  [playing, () => state.value],
  ([p, s]) => {
    acc = 0
    if (p && s === 'ready') raf.resume()
    else raf.pause()
  },
  { immediate: true }
)
watch(row, () => (frame.value = 0))

function selectClip(i: number): void {
  const prev = activeClip.value
  const next = clips.value[i]
  if (!next || i === clipIdx.value) return
  // Keep facing the same way across clips so walk → idle compares one direction.
  const offset = prev ? Math.min(Math.max(0, row.value - prev.row0), next.rows - 1) : 0
  clipIdx.value = i
  row.value = next.row0 + offset
  frame.value = 0
  if (next.fps) fps.value = clampFps(next.fps)
}

function pick(i: number): void {
  playing.value = false
  frame.value = i
}

function onKey(e: KeyboardEvent): void {
  if (state.value !== 'ready' || e.altKey || e.ctrlKey || e.metaKey) return
  const t = e.target as HTMLElement | null
  if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) && (t as HTMLInputElement).type !== 'range') return
  const n = frames.value.length
  if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && n) {
    pick((frame.value + (e.key === 'ArrowLeft' ? n - 1 : 1)) % n)
  } else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !directional.value && shownRows.value.length > 1) {
    const list = shownRows.value
    const i = Math.max(0, list.indexOf(row.value))
    row.value = list[(i + (e.key === 'ArrowUp' ? list.length - 1 : 1)) % list.length]!
  } else if (e.key === ' ' && t?.tagName !== 'BUTTON') {
    playing.value = !playing.value
  } else if ((e.key === 'n' || e.key === 'N') && props.normal) {
    showNormal.value = !showNormal.value
  } else return
  e.preventDefault()
  e.stopPropagation()
}

const stage = ref<HTMLElement | null>(null)
const { width: sw, height: sh } = useElementSize(stage)
const fitZoom = computed(() => {
  const m = meta.value
  if (!m || !sw.value || !sh.value) return 1
  const z = Math.min((sw.value - 48) / m.frame_w, (sh.value - 96) / m.frame_h)
  return Math.max(0.1, pixel.value && z >= 1 ? Math.floor(z) : z)
})
const scale = computed(() => (zoom.value === 'fit' ? fitZoom.value : zoom.value))
const ZOOMS = [1, 2, 3, 4, 6, 8, 12, 16]
function zoomBy(dir: 1 | -1): void {
  const cur = scale.value
  const next = dir > 0 ? ZOOMS.find((z) => z > cur + 1e-6) : [...ZOOMS].reverse().find((z) => z < cur - 1e-6)
  if (next) zoom.value = next
}
const zoomText = computed(() => (Number.isInteger(scale.value) ? `${scale.value}×` : `${scale.value.toFixed(1).replace('.', ',')}×`))

const canvas = ref<HTMLCanvasElement | null>(null)
function draw(): void {
  const c = canvas.value
  const m = meta.value
  const f = frames.value[frame.value]
  const src = source.value
  if (!c || !m || !src || !f) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  ctx.drawImage(src, f.x, f.y, m.frame_w, m.frame_h, 0, 0, m.frame_w, m.frame_h)
}
watch([frame, frames, canvas, source], () => void nextTick(draw), { flush: 'post' })

const boxStyle = computed(() => {
  const m = meta.value
  return m ? { width: `${m.frame_w * scale.value}px`, height: `${m.frame_h * scale.value}px` } : {}
})
const pivotStyle = computed(() => {
  const p = meta.value?.pivot
  return p ? { transform: `translate(${p[0] * scale.value}px, ${p[1] * scale.value}px)` } : null
})

const THUMB = 44
function thumbStyle(f: Frame): Record<string, string> {
  const m = meta.value!
  const s = THUMB / Math.max(m.frame_w, m.frame_h)
  const im = source.value!
  return {
    backgroundImage: `url("${im.src}")`,
    backgroundSize: `${im.naturalWidth * s}px ${im.naturalHeight * s}px`,
    backgroundPosition: `${-f.x * s}px ${-f.y * s}px`,
    width: `${m.frame_w * s}px`,
    height: `${m.frame_h * s}px`
  }
}
const current = computed(() => frames.value[frame.value])
const caption = computed<{ main: string; sub: string; angle: number | null }>(() => {
  const f = current.value
  if (!f) return { main: '', sub: '', angle: null }
  if (directional.value) return { main: f.label, sub: f.sub, angle: f.angle }
  const a = rowAngle(row.value)
  const parts = [a !== null ? angleText(a) : '', `khung ${frame.value + 1}/${frames.value.length}`]
  return { main: rowLabel(row.value), sub: parts.filter(Boolean).join(' · '), angle: a }
})
// Unwrapped so the caption arrow turns the short way (315° → 0° is +45°, not a full spin back).
const capRot = ref(0)
watch(
  () => caption.value.angle,
  (a) => {
    if (a === null) return
    const cur = ((capRot.value % 360) + 360) % 360
    capRot.value += ((a - cur + 540) % 360) - 180
  },
  { immediate: true }
)
</script>

<template>
  <div
    class="vw sprite"
    :class="{ compact }"
    tabindex="0"
    data-own-keys
    aria-label="Sprite sheet — ←/→ đổi khung, ↑/↓ đổi hướng, Space phát/dừng"
    @keydown="onKey"
  >
    <div ref="stage" class="stage" :class="checker ? 'checker' : 'vw-backdrop'">
      <Transition name="vw-swap">
        <div v-if="state === 'loading'" class="vw-state" role="status">
          <LoaderCircle :size="22" class="spin" />
          <span>Đang tải sprite sheet</span>
        </div>
        <div v-else-if="state === 'error'" class="vw-state err" role="alert">
          <CircleAlert :size="22" />
          <strong>Không hiển thị được sprite sheet</strong>
          <span class="msg">{{ error }}</span>
          <button class="btn" @click="load"><RotateCcw /> Thử lại</button>
        </div>
      </Transition>

      <template v-if="state === 'ready' && meta">
        <div class="vw-chip vw-glass num">
          <span><b>{{ meta.frame_w }}×{{ meta.frame_h }}</b> px</span>
          <span v-if="directional"><b>{{ total }}</b> hướng</span>
          <span v-else-if="clips.length"><b>{{ clips.length }}</b> clip × <b>{{ activeClip?.rows }}</b> hướng</span>
          <span v-else><b>{{ rowCount }}</b> hàng × <b>{{ meta.cols }}</b> khung</span>
          <span v-if="meta.preset && !compact">preset <b>{{ meta.preset }}</b></span>
          <Transition name="vw-swap"><span v-if="showNormal" class="nm-tag">Normal map</span></Transition>
        </div>

        <figure class="frame-wrap">
          <div class="box" :style="boxStyle">
            <canvas ref="canvas" :width="meta.frame_w" :height="meta.frame_h" :class="{ pixel }" />
            <span v-if="pivot && pivotStyle" class="pivot" :style="pivotStyle" aria-hidden="true"><i /></span>
          </div>
          <figcaption class="label">
            <span class="num cap-line">
              <ArrowDown
                v-if="caption.angle !== null"
                class="dir-ic"
                :size="13"
                :stroke-width="2.4"
                :style="{ transform: arrowRotation(capRot) }"
                aria-hidden="true"
              />
              <strong>{{ caption.main }}</strong><span v-if="caption.sub" class="sub"> · {{ caption.sub }}</span>
            </span>
          </figcaption>
        </figure>

        <div class="vw-bar vw-glass" role="toolbar" aria-label="Điều khiển sprite">
          <button class="vw-btn" :aria-label="playing ? 'Tạm dừng' : 'Phát'" :title="playing ? 'Tạm dừng (Space)' : 'Phát (Space)'" @click="playing = !playing">
            <Pause v-if="playing" /><Play v-else />
          </button>
          <label class="fps num" title="Tốc độ phát">
            <input v-model.number="fps" class="vw-range" type="range" min="1" max="24" step="1" aria-label="Khung hình mỗi giây" />
            <span>{{ fps }} fps</span>
          </label>
          <span class="sep" />
          <template v-if="!compact">
            <button class="vw-btn" aria-label="Thu nhỏ" title="Thu nhỏ" :disabled="scale <= 1" @click="zoomBy(-1)"><ZoomOut /></button>
            <span class="zoom num">{{ zoomText }}</span>
            <button class="vw-btn" aria-label="Phóng to" title="Phóng to" @click="zoomBy(1)"><ZoomIn /></button>
          </template>
          <button class="vw-btn" :aria-pressed="zoom === 'fit'" title="Vừa khung" aria-label="Vừa khung" @click="zoom = 'fit'"><Scan /></button>
          <span class="sep" />
          <button class="vw-btn" :aria-pressed="pixel" title="Điểm ảnh sắc nét (nearest-neighbour)" @click="pixel = !pixel">
            <span class="px-ic" aria-hidden="true" />
            <span v-if="!compact">Pixel</span>
          </button>
          <button
            v-if="normal"
            class="vw-btn"
            :class="{ err: normalState === 'error' }"
            :aria-pressed="showNormal"
            aria-label="Normal map"
            :title="normalState === 'error' ? 'Không tải được normal map — bấm để thử lại' : 'Xem normal map (N)'"
            @click="showNormal = !showNormal"
          >
            <LoaderCircle v-if="normalState === 'loading'" class="spin" />
            <span v-else class="nm-ic" aria-hidden="true" />
            <span v-if="!compact">Normal</span>
          </button>
          <button class="vw-btn" :aria-pressed="pivot" title="Hiện pivot" aria-label="Hiện pivot" :disabled="!meta.pivot" @click="pivot = !pivot"><Crosshair /></button>
          <button class="vw-btn" :aria-pressed="checker" title="Nền ô kẻ (trong suốt)" aria-label="Nền ô kẻ" @click="checker = !checker"><SquareDashed /></button>
        </div>
      </template>
    </div>

    <div v-if="state === 'ready' && meta" class="strip">
      <div v-if="clips.length > 1" class="clips" role="radiogroup" aria-label="Chọn clip">
        <button
          v-for="(c, i) in clips"
          :key="`${c.name}-${c.row0}`"
          role="radio"
          :aria-checked="clipIdx === i"
          :class="{ on: clipIdx === i }"
          :title="`${c.frames} khung · ${c.fps ?? fps} fps${c.loop === false ? ' · chạy 1 lần' : ''}`"
          @click="selectClip(i)"
        >
          {{ clipLabel(c) }}
          <span class="num cn">{{ c.frames }}</span>
        </button>
      </div>
      <div v-if="!directional && shownRows.length > 1" class="rows" role="radiogroup" aria-label="Chọn hướng (↑/↓)">
        <button v-for="r in shownRows" :key="r" role="radio" :aria-checked="row === r" :class="{ on: row === r }" :title="rowTitle(r)" @click="row = r">
          <ArrowDown
            v-if="rowAngle(r) !== null"
            class="dir-ic"
            :size="12"
            :stroke-width="2.4"
            :style="{ transform: arrowRotation(rowAngle(r) ?? 0) }"
            aria-hidden="true"
          />
          {{ rowLabel(r) }}
        </button>
      </div>
      <div class="frames" role="listbox" :aria-label="directional ? 'Các hướng' : 'Các khung (←/→)'">
        <button
          v-for="(f, i) in frames"
          :key="`${row}-${i}`"
          role="option"
          :aria-selected="frame === i"
          class="fr"
          :class="{ on: frame === i }"
          :title="f.sub ? `${f.label} · ${f.sub}` : f.label"
          @click="pick(i)"
        >
          <span class="th-wrap checker"><span class="th" :class="{ pixel }" :style="thumbStyle(f)" /></span>
          <span class="fl">
            <ArrowDown
              v-if="directional && f.angle !== null"
              class="dir-ic"
              :size="10"
              :stroke-width="2.6"
              :style="{ transform: arrowRotation(f.angle) }"
              aria-hidden="true"
            />
            {{ f.label }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sprite {
  display: flex;
  flex-direction: column;
}
.stage {
  position: relative;
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  transition: background-color var(--t-base);
}
.frame-wrap {
  margin: 0 0 40px;
  display: grid;
  justify-items: center;
  gap: 10px;
}
.box {
  position: relative;
}
canvas {
  display: block;
  width: 100%;
  height: 100%;
}
canvas.pixel,
.th.pixel {
  image-rendering: pixelated;
}
.pivot {
  position: absolute;
  top: 0;
  left: 0;
  width: 0;
  height: 0;
  pointer-events: none;
  transition: transform var(--t-base) var(--ease-spring);
}
.pivot::before,
.pivot::after {
  content: '';
  position: absolute;
  background: var(--st-human);
  box-shadow: 0 0 0 0.5px rgba(255, 255, 255, 0.7);
}
.pivot::before {
  left: -12px;
  top: -0.5px;
  width: 24px;
  height: 1px;
}
.pivot::after {
  top: -12px;
  left: -0.5px;
  width: 1px;
  height: 24px;
}
.pivot i {
  position: absolute;
  left: -4px;
  top: -4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid var(--st-human);
}
.label {
  min-height: 18px;
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.label strong {
  color: var(--text);
  font-weight: 650;
}
.sub {
  color: var(--text-3);
}
.fps {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 6px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.fps span {
  min-width: 38px;
}
.zoom {
  min-width: 30px;
  text-align: center;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.px-ic {
  width: 13px;
  height: 13px;
  background:
    linear-gradient(currentColor 0 0) 0 0 / 6px 6px no-repeat,
    linear-gradient(currentColor 0 0) 7px 7px / 6px 6px no-repeat;
  border-radius: 1px;
  opacity: 0.9;
}
.strip {
  flex: none;
  display: grid;
  gap: 8px;
  padding: 10px 12px 12px;
  border-top: 0.5px solid var(--separator);
  background: var(--bg-elevated);
}
.rows {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.rows button {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 999px;
  background: var(--bg-sunken);
  color: var(--text-2);
  font-size: var(--fs-caption);
  font-weight: 600;
  cursor: pointer;
  transition: background-color var(--t-fast), color var(--t-fast);
}
.rows button.on {
  color: var(--text-on-accent);
  background: var(--accent);
}
.frames {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.fr {
  flex: none;
  display: grid;
  justify-items: center;
  gap: 4px;
  min-width: 56px;
  padding: 5px 4px 4px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    transform var(--t-fast) var(--ease-spring);
}
.fr:hover {
  background: var(--bg-hover);
}
.fr:active {
  transform: scale(0.95);
}
.fr.on {
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1.5px var(--accent);
}
.th-wrap {
  display: block;
  border-radius: 4px;
  overflow: hidden;
}
.th {
  display: block;
  background-repeat: no-repeat;
}
.fl {
  max-width: 84px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fr.on .fl {
  color: var(--accent);
}
.compact .frame-wrap {
  margin-bottom: 36px;
}
.compact .vw-range {
  width: 64px;
}
.compact .strip {
  padding: 6px 8px 8px;
}
.sprite:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.cap-line {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dir-ic {
  flex: none;
  transition: transform var(--t-base) var(--ease-spring);
}
.nm-tag {
  color: var(--accent);
  font-weight: 600;
}
.nm-ic {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: conic-gradient(from 90deg, #8080ff, #ff8080, #80ff80, #8080ff);
  box-shadow: inset 0 0 0 0.5px rgba(0, 0, 0, 0.25);
}
.vw-btn.err {
  color: var(--st-fail);
}
.vw-bar .spin {
  animation: spin 0.9s linear infinite;
}
.clips {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.clips button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 11px;
  border: 0;
  border-radius: 8px;
  background: var(--bg-sunken);
  color: var(--text-2);
  font-size: var(--fs-callout);
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--hairline);
  transition:
    background-color var(--t-fast),
    color var(--t-fast),
    box-shadow var(--t-fast),
    transform var(--t-fast) var(--ease-spring);
}
.clips button:active {
  transform: scale(0.96);
}
.clips button.on {
  color: var(--text);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), inset 0 0 0 1.5px var(--accent);
}
.cn {
  font-size: 10.5px;
  color: var(--text-3);
}
.rows button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.fl {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
</style>
