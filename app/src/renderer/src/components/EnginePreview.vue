<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { AlertTriangle, CircleCheck, CircleX, LoaderCircle, MonitorPlay, Play, Square } from '@lucide/vue'
import type { EnginePreviewJob, PreviewEngine, PreviewEngines } from '@/api/enginePreview'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import BlobImage from './BlobImage.vue'
import ElapsedTimer from './ElapsedTimer.vue'

const props = defineProps<{ stepId: string; name?: string; compact?: boolean }>()

const toasts = useToasts()
const engines = ref<PreviewEngines['engines'] | null>(null)
const exportable = ref(false)
const engine = ref<PreviewEngine>('godot')
const job = ref<EnginePreviewJob | null>(null)
const starting = ref(false)
const open = ref(false)
const button = ref<HTMLButtonElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0 })
let timer: number | undefined

const LABEL: Record<PreviewEngine, string> = { godot: 'Godot', unity: 'Unity' }
const running = computed(() => starting.value || job.value?.status === 'queued' || job.value?.status === 'running')
const anyAvailable = computed(() => !!engines.value && Object.values(engines.value).some((e) => e.available))
const visible = computed(() => exportable.value && anyAvailable.value)

watch(
  () => props.stepId,
  async (id) => {
    stopPolling()
    job.value = null
    exportable.value = false
    try {
      const [opts, list] = await Promise.all([window.studio?.asset?.bundleOptions(id), api().enginePreviewEngines()])
      if (id !== props.stepId) return
      engines.value = list.engines
      exportable.value = !!opts?.engines?.length
      if (!list.engines[engine.value].available) engine.value = list.engines.godot.available ? 'godot' : 'unity'
    } catch {
      /* no engine export for this step, or an older core without engine preview → no button */
    }
  },
  { immediate: true }
)

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

// Same PPU / pixel choice as the "Xuất cho engine" menu, so the preview shows what that export produces.
function exportOpts(): { ppu: number; pixel: boolean | null } {
  const ppu = Number(readPref('export:ppu'))
  const pixel = readPref('export:pixel')
  return { ppu: Number.isFinite(ppu) && ppu > 0 && ppu <= 10000 ? ppu : 100, pixel: pixel === 'on' ? true : pixel === 'off' ? false : null }
}

async function run(): Promise<void> {
  starting.value = true
  try {
    job.value = await api().startEnginePreview(props.stepId, { engine: engine.value, name: props.name, ...exportOpts() })
    poll()
  } catch (e) {
    toasts.error(e, `Không mở được trong ${LABEL[engine.value]}`)
  } finally {
    starting.value = false
  }
}

function poll(): void {
  stopPolling()
  timer = window.setTimeout(async () => {
    const cur = job.value
    if (!cur) return
    try {
      job.value = await api().enginePreviewJob(cur.id)
    } catch (e) {
      toasts.error(e, 'Mất dấu lượt xem trong engine')
      return
    }
    if (running.value) return poll()
    if (!open.value) notify(job.value)
  }, 1000)
}

function stopPolling(): void {
  window.clearTimeout(timer)
  timer = undefined
}

function notify(j: EnginePreviewJob): void {
  if (j.status === 'succeeded') toasts.push(`${LABEL[j.engine]}: mở được, ${j.errors.length ? `${j.errors.length} lỗi` : 'không lỗi'}`, j.errors.length ? 'error' : 'info')
  else if (j.status === 'failed') toasts.push(`${LABEL[j.engine]}: không mở được (${j.errors[0] ?? 'lỗi không rõ'})`, 'error', 6000)
}

async function cancel(): Promise<void> {
  if (!job.value) return
  try {
    job.value = await api().cancelEnginePreview(job.value.id)
  } catch (e) {
    toasts.error(e, 'Không hủy được')
  }
}

const summary = computed(() => {
  const j = job.value
  if (!j || running.value) return ''
  const parts = [`${j.seconds.toLocaleString('vi-VN')} s`]
  if (j.target) parts.push(j.target)
  if (j.status === 'cancelled') return `Đã hủy · ${parts.join(' · ')}`
  return `${j.errors.length ? `${j.errors.length} lỗi` : 'Không lỗi'}${j.warnings.length ? `, ${j.warnings.length} cảnh báo` : ''} · ${parts.join(' · ')}`
})
const statusTone = computed(() => {
  const j = job.value
  if (!j || running.value || j.status === 'cancelled') return 'muted'
  return j.status === 'failed' || j.errors.length ? 'bad' : j.warnings.length ? 'warn' : 'good'
})

async function toggle(): Promise<void> {
  if (open.value) return close()
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  pos.value = { left: Math.max(8, Math.min(r.left, window.innerWidth - 448)), top: r.bottom + 4 }
  open.value = true
  await nextTick()
  panel.value?.focus()
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

onClickOutside(panel, () => close(false), { ignore: [button] })
useEventListener(window, 'resize', () => close(false))
onBeforeUnmount(stopPolling)
</script>

<template>
  <template v-if="visible">
    <button
      ref="button"
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact }"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-label="compact ? 'Xem trong engine' : undefined"
      title="Xuất rồi mở thật trong Godot / Unity: kiểm lỗi import và chụp màn hình"
      @click="toggle"
    >
      <LoaderCircle v-if="running" class="spin" />
      <MonitorPlay v-else />
      <span v-if="!compact">Xem trong engine</span>
    </button>
    <Teleport to="body">
      <Transition name="menu">
        <div
          v-if="open"
          ref="panel"
          class="ep"
          role="dialog"
          aria-label="Xem trong engine"
          tabindex="-1"
          :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
          @keydown.esc.stop="close()"
        >
          <div class="ep-head">
            <div class="ep-seg" role="radiogroup" aria-label="Engine">
              <button
                v-for="e in (['godot', 'unity'] as PreviewEngine[])"
                :key="e"
                type="button"
                role="radio"
                :aria-checked="engine === e"
                :class="{ on: engine === e }"
                :disabled="running || !engines?.[e].available"
                :title="engines?.[e].reason ?? engines?.[e].path"
                @click="engine = e"
              >
                {{ LABEL[e] }}
              </button>
            </div>
            <button v-if="!running" type="button" class="btn btn-primary" @click="run">
              <Play :size="14" />
              <span>{{ job ? 'Chạy lại' : 'Mở' }}</span>
            </button>
            <button v-else type="button" class="btn" :disabled="starting" @click="cancel">
              <Square :size="13" />
              <span>Hủy</span>
            </button>
          </div>

          <p v-if="running" class="ep-line caption">
            <LoaderCircle :size="13" class="spin" aria-hidden="true" />
            <span>Đang xuất và mở trong {{ LABEL[job?.engine ?? engine] }}{{ job?.status === 'queued' ? ' (chờ lượt trước)' : '' }}</span>
            <ElapsedTimer v-if="job" :from="job.started_at" />
          </p>
          <p v-else-if="!job" class="ep-line caption">
            {{ engine === 'unity' ? 'Unity chạy nền 1–3 phút, cần đóng project Unity nếu đang mở.' : 'Godot chạy vài giây, mở cửa sổ ẩn để chụp.' }}
          </p>

          <template v-if="job && !running">
            <p class="ep-line ep-status" :class="statusTone" role="status">
              <CircleCheck v-if="statusTone === 'good'" :size="14" aria-hidden="true" />
              <AlertTriangle v-else-if="statusTone === 'warn'" :size="14" aria-hidden="true" />
              <CircleX v-else-if="statusTone === 'bad'" :size="14" aria-hidden="true" />
              <span>{{ summary }}</span>
            </p>
            <div v-if="job.image" class="ep-shot">
              <BlobImage :hash="job.image.hash" full contain :alt="`Ảnh chụp trong ${LABEL[job.engine]}`" />
            </div>
            <p v-else-if="job.note" class="ep-line caption">{{ job.note }}</p>
            <ul v-if="job.errors.length || job.warnings.length" class="ep-issues">
              <li v-for="(m, i) in job.errors" :key="`e${i}`" class="bad">{{ m }}</li>
              <li v-for="(m, i) in job.warnings" :key="`w${i}`" class="warn">{{ m }}</li>
            </ul>
            <details v-if="job.log_tail" class="ep-log">
              <summary class="caption">Log engine</summary>
              <pre>{{ job.log_tail }}</pre>
            </details>
          </template>
        </div>
      </Transition>
    </Teleport>
  </template>
</template>

<style scoped>
.ep {
  position: fixed;
  z-index: 60;
  display: grid;
  gap: 8px;
  width: min(440px, calc(100vw - 16px));
  max-height: calc(100vh - 80px);
  overflow: auto;
  padding: 10px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: top left;
}
.ep-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ep-seg {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
}
.ep-seg button {
  height: 26px;
  padding: 0 12px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  transition: background-color var(--t-fast), color var(--t-fast);
}
.ep-seg button.on {
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow-1);
}
.ep-seg button:disabled {
  opacity: 0.45;
}
.ep-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--text-2);
}
.ep-status.good {
  color: var(--st-ok);
}
.ep-status.warn {
  color: var(--st-waiting);
}
.ep-status.bad {
  color: var(--danger);
}
.ep-shot {
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
}
.ep-shot :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.ep-issues {
  display: grid;
  gap: 3px;
  max-height: 160px;
  margin: 0;
  padding: 0 0 0 16px;
  overflow: auto;
  font-size: var(--fs-caption);
  word-break: break-word;
}
.ep-issues .bad {
  color: var(--danger);
}
.ep-issues .warn {
  color: var(--st-waiting);
}
.ep-log pre {
  max-height: 180px;
  margin: 4px 0 0;
  padding: 6px 8px;
  overflow: auto;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  font-size: 11px;
  white-space: pre-wrap;
}
.spin {
  animation: spin 1s linear infinite;
}
.menu-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    scale var(--t-fast) var(--ease-out);
}
.menu-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.menu-enter-from {
  opacity: 0;
  scale: 0.97;
}
.menu-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
