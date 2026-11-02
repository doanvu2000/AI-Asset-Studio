<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDocumentVisibility } from '@vueuse/core'
import { ArrowUpToLine, Gpu, Play } from '@lucide/vue'
import type { GpuSlot, GpuState } from '@/api/types'
import { api, useConnection } from '@/stores/connection'
import { useGpu } from '@/stores/gpu'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'
import { useNow } from '@/composables/clock'
import { stepName } from '@/composables/format'

// gpu.updated events refresh at once; the poll only catches queue moves that come without one.
const POLL_MS = 10_000
const CHOICES = [5, 15, 30]

const recipes = useRecipes()
const toasts = useToasts()
const visible = useDocumentVisibility()
const now = useNow()
const gpu = useGpu()
const conn = useConnection()
const { state } = storeToRefs(gpu)
const busy = ref(false)
const unwatch = gpu.watch()
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true

async function poll(): Promise<void> {
  try {
    if (visible.value === 'visible' && (conn.socket !== 'open' || !state.value)) await gpu.load()
  } catch {
    /* keep the last reading; the core may be restarting */
  }
  if (alive) timer = setTimeout(poll, POLL_MS)
}
onMounted(poll)
onBeforeUnmount(() => {
  alive = false
  clearTimeout(timer)
  unwatch()
})

async function act(fn: () => Promise<GpuState>, fail: string): Promise<void> {
  busy.value = true
  try {
    state.value = await fn()
  } catch (e) {
    toasts.error(e, fail)
  } finally {
    busy.value = false
  }
}

const lend = (minutes: number) => act(() => api().yieldGpu(minutes), 'Không nhường được GPU')
const resume = () => act(() => api().resumeGpu(), 'Không lấy lại được GPU')
const toFront = (runId: string) => act(() => api().prioritizeGpu(runId, { position: 0 }), 'Không đưa lên đầu được')

const left = computed(() => Math.max(0, Math.round((state.value?.until ?? 0) - now.value)))
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
const label = (s: GpuSlot) => s.title || recipes.byId(s.recipe_id)?.name || 'Lượt chạy'
const step = (s: GpuSlot) => stepName({ kind: s.kind, key: s.step_key })
const waited = (s: GpuSlot) => (s.waiting_s == null ? '' : s.waiting_s < 60 ? `${Math.round(s.waiting_s)} giây` : `${Math.round(s.waiting_s / 60)} phút`)

const status = computed(() => {
  const s = state.value
  if (!s) return ''
  if (s.yielding) {
    if (s.running.length) return 'Đang nhường · chờ bước hiện tại xong'
    return s.freed ? 'Đang nhường · đã giải phóng VRAM' : 'Đang nhường'
  }
  if (s.running.length) return `Đang dùng: ${label(s.running[0]!)} · ${step(s.running[0]!)}${s.running[0]!.lane === 'blender' ? ' (Blender)' : ''}`
  return 'Rảnh'
})
</script>

<template>
  <section v-if="state" class="gpu card" :class="{ yielding: state.yielding }" aria-label="Hàng đợi GPU">
    <div class="head">
      <Gpu :size="15" class="ic" aria-hidden="true" />
      <span class="title">GPU</span>
      <span class="state callout" :title="status">{{ status }}</span>
      <template v-if="state.yielding">
        <span class="left num" :title="'Tự dùng lại sau ' + clock(left)">{{ clock(left) }}</span>
        <button class="btn btn-plain act" :disabled="busy" @click="resume"><Play /> Dùng lại ngay</button>
      </template>
      <span v-else class="lend">
        <span class="caption">Nhường GPU</span>
        <span class="seg" role="group" aria-label="Nhường GPU trong">
          <button v-for="m in CHOICES" :key="m" type="button" :disabled="busy" :title="`Dừng nhận bước GPU mới trong ${m} phút để app khác dùng VRAM`" @click="lend(m)">{{ m }} phút</button>
        </span>
      </span>
    </div>
    <ol v-if="state.queued.length" v-auto-animate class="queue">
      <li v-for="(q, i) in state.queued" :key="q.step_id" class="row">
        <span class="pos num caption">{{ i + 1 }}</span>
        <RouterLink :to="`/runs/${q.run_id}`" class="name">{{ label(q) }}</RouterLink>
        <span class="caption step">{{ step(q) }}</span>
        <span class="caption num wait">{{ waited(q) }}</span>
        <button class="btn btn-plain front" :disabled="busy || i === 0 || state.queued[0]!.run_id === q.run_id" title="Đưa lượt chạy này lên đầu hàng đợi GPU" aria-label="Đưa lên đầu" @click="toFront(q.run_id)">
          <ArrowUpToLine />
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.gpu {
  display: grid;
  gap: 4px;
  padding: 8px 16px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  min-height: 32px;
}
.ic {
  flex: none;
  color: var(--text-3);
  transition: color var(--t-base);
}
.yielding .ic {
  color: var(--st-waiting);
}
.title {
  font-weight: 600;
}
.state {
  flex: 1;
  min-width: 0;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.left {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--st-waiting);
}
.lend {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.act,
.front {
  height: 24px;
  padding: 0 8px;
  font-size: var(--fs-callout);
}
.act svg,
.front svg {
  width: 13px;
  height: 13px;
}
.queue {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 34px;
  border-top: 0.5px solid var(--separator);
}
.pos {
  width: 16px;
  text-align: right;
  color: var(--text-3);
}
.name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: inherit;
  text-decoration: none;
  font-weight: 600;
}
.name:hover {
  color: var(--accent);
}
.step {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}
.wait {
  color: var(--text-3);
}
</style>
