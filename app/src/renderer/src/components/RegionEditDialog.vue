<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { Brush, Eraser, GitCompareArrows, LoaderCircle, Play, Redo2, Trash2, Undo2, WandSparkles, X } from '@lucide/vue'
import type { VersionRow } from '@/api/types'
import { api, blobUrl } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useVersions } from '@/stores/versions'
import { useFocusTrap } from '@/composables/focusTrap'
import CompareView, { type CompareItem } from './CompareView.vue'

interface Stroke {
  erase: boolean
  size: number
  pts: [number, number][]
}

const props = defineProps<{ version: VersionRow }>()
const open = defineModel<boolean>('open', { required: true })

const toasts = useToasts()
const versions = useVersions()
const root = ref<HTMLElement | null>(null)
const trap = useFocusTrap(root, open)

const img = ref<HTMLImageElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const natural = ref({ w: 0, h: 0 })
const strokes = ref<Stroke[]>([])
const redo = ref<Stroke[]>([])
const tool = ref<'brush' | 'eraser'>('brush')
const brush = ref(5)
const feather = ref(3)
const prompt = ref('')
const phase = ref<'edit' | 'running' | 'done'>('edit')
const resultId = ref<string | null>(null)
const compareOpen = ref(false)
let drawing: Stroke | null = null
let poll = 0
let before = new Set<string>()

const brushPx = computed(() => Math.max(2, Math.round((Math.max(natural.value.w, natural.value.h) * brush.value) / 100)))
const hasMask = computed(() => strokes.value.some((s) => !s.erase))
const canRun = computed(() => phase.value === 'edit' && hasMask.value && prompt.value.trim().length > 0)
const result = computed(() => (resultId.value ? versions.map.get(resultId.value) : undefined))
const compareItems = computed<CompareItem[] | null>(() => {
  if (!compareOpen.value || !result.value) return null
  const mk = (v: VersionRow, label: string): CompareItem => ({ key: v.id, name: `${label} — ${v.asset_name}`, meta: v.id === props.version.id ? 'Bản gốc' : 'Bản đã sửa', status: v.status, lead: v.blob, siblings: [v.blob] })
  return [mk(versions.map.get(props.version.id) ?? props.version, 'Trước'), mk(result.value, 'Sau')]
})

function paint(ctx: CanvasRenderingContext2D, color: string): void {
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const s of strokes.value) {
    ctx.globalCompositeOperation = s.erase ? 'destination-out' : 'source-over'
    ctx.strokeStyle = ctx.fillStyle = color
    ctx.lineWidth = s.size
    const first = s.pts[0]
    if (!first) continue
    ctx.beginPath()
    ctx.moveTo(first[0], first[1])
    for (const p of s.pts.slice(1)) ctx.lineTo(p[0], p[1])
    if (s.pts.length === 1) ctx.lineTo(first[0] + 0.01, first[1])
    ctx.stroke()
  }
  ctx.globalCompositeOperation = 'source-over'
}

function redraw(): void {
  const c = canvas.value
  const ctx = c?.getContext('2d')
  if (!c || !ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  paint(ctx, 'rgba(255, 70, 70, 0.55)')
}

function exportMask(): string {
  const layer = document.createElement('canvas')
  layer.width = natural.value.w
  layer.height = natural.value.h
  const lctx = layer.getContext('2d')!
  paint(lctx, '#ffffff')
  const out = document.createElement('canvas')
  out.width = layer.width
  out.height = layer.height
  const octx = out.getContext('2d')!
  octx.fillStyle = '#000000'
  octx.fillRect(0, 0, out.width, out.height)
  octx.drawImage(layer, 0, 0)
  return out.toDataURL('image/png').split(',', 2)[1] ?? ''
}

function point(e: PointerEvent): [number, number] {
  const c = canvas.value!
  const r = c.getBoundingClientRect()
  return [((e.clientX - r.left) * c.width) / r.width, ((e.clientY - r.top) * c.height) / r.height]
}

function down(e: PointerEvent): void {
  if (phase.value !== 'edit' || e.button !== 0) return
  canvas.value?.setPointerCapture(e.pointerId)
  drawing = { erase: tool.value === 'eraser', size: brushPx.value, pts: [point(e)] }
  strokes.value = [...strokes.value, drawing]
  redo.value = []
  redraw()
}

function move(e: PointerEvent): void {
  if (!drawing) return
  drawing.pts.push(point(e))
  redraw()
}

function up(): void {
  drawing = null
}

function undo(): void {
  const s = strokes.value[strokes.value.length - 1]
  if (!s) return
  strokes.value = strokes.value.slice(0, -1)
  redo.value = [...redo.value, s]
  redraw()
}

function redoStroke(): void {
  const s = redo.value[redo.value.length - 1]
  if (!s) return
  redo.value = redo.value.slice(0, -1)
  strokes.value = [...strokes.value, s]
  redraw()
}

function clearMask(): void {
  strokes.value = []
  redo.value = []
  redraw()
}

function onLoad(): void {
  const el = img.value
  if (!el) return
  natural.value = { w: el.naturalWidth, h: el.naturalHeight }
  void Promise.resolve().then(redraw)
}

function stopPoll(): void {
  window.clearInterval(poll)
  poll = 0
}

async function run(): Promise<void> {
  if (!canRun.value) return
  phase.value = 'running'
  resultId.value = null
  before = new Set(children().map((v) => v.id))
  try {
    const { run_id } = await api().regionEdit(props.version.id, { mask: exportMask(), prompt: prompt.value.trim(), feather: feather.value })
    stopPoll()
    poll = window.setInterval(async () => {
      try {
        const d = await api().getRun(run_id)
        if (['failed', 'cancelled', 'interrupted'].includes(d.run.status)) {
          stopPoll()
          phase.value = 'edit'
          toasts.push(d.run.error || 'Lượt sửa không hoàn tất', 'error', 6000)
        } else if (d.run.status === 'succeeded') {
          await versions.load()
          finish()
        }
      } catch {
        /* next tick retries */
      }
    }, 2500)
  } catch (e) {
    phase.value = 'edit'
    toasts.error(e, 'Không chạy được')
  }
}

const children = (): VersionRow[] => versions.list.filter((v) => v.parent_id === props.version.id)

function finish(): void {
  const child = children().find((v) => !before.has(v.id))
  if (!child) return
  stopPoll()
  resultId.value = child.id
  phase.value = 'done'
}

// The live version.updated event usually lands before the run poll notices it.
watch(
  () => children().length,
  () => {
    if (phase.value === 'running') finish()
  }
)

function again(): void {
  phase.value = 'edit'
  compareOpen.value = false
}

async function review(key: string, action: 'approve' | 'reject'): Promise<void> {
  try {
    await versions.setStatus(key, action)
  } catch (e) {
    toasts.error(e, 'Không đổi được trạng thái')
  }
}

function close(): void {
  if (phase.value === 'running') return
  open.value = false
}

watch(open, (on) => {
  if (on) return
  stopPoll()
  strokes.value = []
  redo.value = []
  prompt.value = ''
  phase.value = 'edit'
  resultId.value = null
  compareOpen.value = false
})

useEventListener(document, 'keydown', (e: KeyboardEvent) => {
  if (!open.value || compareOpen.value) return
  if (trap.onTab(e)) return
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && phase.value === 'edit' && !(e.target instanceof HTMLTextAreaElement)) {
    e.preventDefault()
    undo()
  }
})
onBeforeUnmount(stopPoll)
</script>

<template>
  <Teleport to="body">
    <Transition name="re">
      <div v-if="open" class="scrim" @mousedown.self="close">
        <div ref="root" class="sheet" role="dialog" aria-modal="true" aria-labelledby="re-title">
          <header class="head">
            <WandSparkles :size="16" class="h-ic" aria-hidden="true" />
            <div class="h-text">
              <h2 id="re-title" class="title-2">Sửa cục bộ</h2>
              <p class="caption">Tô vùng cần sửa, nhập lệnh — ngoài vùng tô giữ nguyên từng pixel; kết quả là bản nháp mới của cùng asset</p>
            </div>
            <button type="button" class="btn btn-plain btn-icon" title="Đóng" aria-label="Đóng" :disabled="phase === 'running'" @click="close"><X :size="16" aria-hidden="true" /></button>
          </header>

          <div class="body">
            <div class="stage checker">
              <div class="frame">
                <img ref="img" :src="blobUrl(version.blob.hash)" alt="Ảnh đang sửa" draggable="false" @load="onLoad" />
                <canvas
                  ref="canvas"
                  class="mask"
                  :class="{ locked: phase !== 'edit' }"
                  :width="natural.w || 1"
                  :height="natural.h || 1"
                  aria-label="Vùng tô cần sửa"
                  @pointerdown="down"
                  @pointermove="move"
                  @pointerup="up"
                  @pointercancel="up"
                />
              </div>
            </div>

            <aside class="side">
              <section>
                <h3 class="sec">Vùng sửa</h3>
                <div class="tools" role="group" aria-label="Công cụ vẽ">
                  <button type="button" class="btn" :class="{ on: tool === 'brush' }" :aria-pressed="tool === 'brush'" :disabled="phase !== 'edit'" @click="tool = 'brush'"><Brush /> Cọ</button>
                  <button type="button" class="btn" :class="{ on: tool === 'eraser' }" :aria-pressed="tool === 'eraser'" :disabled="phase !== 'edit'" @click="tool = 'eraser'"><Eraser /> Tẩy</button>
                  <button type="button" class="btn btn-plain btn-icon" title="Hoàn tác (Ctrl+Z)" aria-label="Hoàn tác" :disabled="phase !== 'edit' || !strokes.length" @click="undo"><Undo2 /></button>
                  <button type="button" class="btn btn-plain btn-icon" title="Làm lại" aria-label="Làm lại" :disabled="phase !== 'edit' || !redo.length" @click="redoStroke"><Redo2 /></button>
                  <button type="button" class="btn btn-plain btn-icon" title="Xoá hết vùng tô" aria-label="Xoá vùng tô" :disabled="phase !== 'edit' || !strokes.length" @click="clearMask"><Trash2 /></button>
                </div>
                <label class="row">
                  <span class="caption">Cỡ cọ</span>
                  <input v-model.number="brush" class="vw-range" type="range" min="1" max="30" step="1" :disabled="phase !== 'edit'" aria-label="Cỡ cọ" />
                  <span class="num caption">{{ brushPx }}px</span>
                </label>
                <label class="row">
                  <span class="caption">Mép mềm</span>
                  <input v-model.number="feather" class="vw-range" type="range" min="0" max="16" step="1" :disabled="phase !== 'edit'" aria-label="Độ mềm mép" />
                  <span class="num caption">{{ feather }}px</span>
                </label>
              </section>

              <section>
                <h3 class="sec">Lệnh sửa</h3>
                <textarea v-model="prompt" class="prompt" rows="4" placeholder="Ví dụ: đổi màu khiên sang đỏ" :disabled="phase !== 'edit'" aria-label="Lệnh sửa" />
              </section>

              <section v-if="phase === 'running'" class="note" role="status">
                <LoaderCircle :size="16" class="spin" /> Đang sửa vùng đã tô… (vài chục giây)
              </section>
              <section v-else-if="phase === 'done' && result" class="note ok" role="status">
                <span>Đã tạo bản nháp mới từ bản này.</span>
                <div class="acts">
                  <button type="button" class="btn btn-primary" @click="compareOpen = true"><GitCompareArrows /> So sánh trước / sau</button>
                  <button type="button" class="btn" @click="again">Sửa tiếp</button>
                </div>
              </section>
            </aside>
          </div>

          <footer class="foot">
            <span class="caption hint">{{ hasMask ? '' : 'Tô lên ảnh vùng muốn đổi' }}</span>
            <span class="grow" />
            <button type="button" class="btn btn-plain" :disabled="phase === 'running'" @click="close">Đóng</button>
            <button type="button" class="btn btn-primary" :disabled="!canRun" @click="run"><Play /> Chạy</button>
          </footer>
        </div>
      </div>
    </Transition>
    <CompareView :items="compareItems" @close="compareOpen = false" @review="review" />
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
  padding: 5vh 16px 16px;
  background: var(--scrim);
}
.sheet {
  width: min(980px, 100%);
  max-height: 90vh;
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
.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
}
.stage {
  min-height: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  overflow: auto;
}
.frame {
  position: relative;
  line-height: 0;
  max-width: 100%;
}
.frame img {
  display: block;
  max-width: 100%;
  max-height: 68vh;
  user-select: none;
  -webkit-user-drag: none;
}
.mask {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: crosshair;
  touch-action: none;
}
.mask.locked {
  cursor: default;
  pointer-events: none;
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
.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}
.tools .on {
  background: var(--accent-soft, var(--bg-hover));
  color: var(--accent);
}
.row {
  display: grid;
  grid-template-columns: 64px 1fr 44px;
  align-items: center;
  gap: 8px;
}
.prompt {
  width: 100%;
  resize: vertical;
  padding: 8px 10px;
  border-radius: var(--r-md);
  border: 0.5px solid var(--separator);
  background: var(--bg-input, var(--bg));
  color: var(--text);
  font: inherit;
}
.note {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-hover);
  color: var(--text-2);
}
.note.ok {
  color: var(--st-ok);
}
.acts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-top: 0.5px solid var(--separator);
}
.grow {
  flex: 1;
}
.hint {
  color: var(--text-3);
}
.spin {
  animation: spin 1s linear infinite;
}
.re-enter-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.re-enter-active .sheet {
  transition: transform var(--t-base) var(--ease-spring);
}
.re-leave-active {
  transition: opacity 120ms var(--ease-in);
}
.re-enter-from,
.re-leave-to {
  opacity: 0;
}
.re-enter-from .sheet {
  transform: translateY(-8px) scale(0.98);
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
  .re-enter-active,
  .re-enter-active .sheet,
  .re-leave-active {
    transition: none;
  }
}
</style>
