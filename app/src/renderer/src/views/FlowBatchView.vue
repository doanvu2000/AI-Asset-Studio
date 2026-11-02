<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, ChevronRight, CircleAlert, CircleStop, LoaderCircle, RotateCw } from '@lucide/vue'
import type { FlowBatchDetail, FlowBatchRun, WorkflowStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import EtaChip from '@/components/EtaChip.vue'
import FlowStatusBadge from '@/components/FlowStatusBadge.vue'
import RelTime from '@/components/RelTime.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useFlows } from '@/stores/flows'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

// Events refresh the batch; polling is only the fallback while the event socket is down.
const POLL_MS = 2000
const EVENT_DEBOUNCE_MS = 400

const props = defineProps<{ id: string }>()
const flows = useFlows()
const conn = useConnection()
const toasts = useToasts()

const batch = ref<FlowBatchDetail | null>(null)
const loadError = ref<string | null>(null)
const busy = ref<'cancel' | 'retry' | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true

const live = computed(() => batch.value?.status === 'running' || batch.value?.status === 'awaiting_human')
const count = (s: WorkflowStatus): number => batch.value?.counts[s] ?? 0
const failed = computed(() => count('failed'))
const active = computed(() => count('queued') + count('running') + count('awaiting_human'))
const total = computed(() => batch.value?.total ?? 0)

async function load(): Promise<void> {
  clearTimeout(timer)
  try {
    const d = await api().flowBatch(props.id)
    if (!alive) return
    batch.value = d
    loadError.value = null
    if (!flows.defsLoaded) void flows.loadDefs()
  } catch (e) {
    if (!alive) return
    loadError.value = (e as Error).message
  }
  if (alive && live.value && !conn.online) timer = setTimeout(() => void load(), POLL_MS)
}

const memberSig = computed(() =>
  flows.runList
    .filter((r) => r.batch_id === props.id)
    .map((r) => `${r.id}:${r.updated_at}`)
    .join()
)
watch(memberSig, () => {
  if (!live.value && batch.value) return
  clearTimeout(timer)
  timer = setTimeout(() => void load(), EVENT_DEBOUNCE_MS)
})
watch(
  () => conn.online,
  (on) => {
    if (batch.value || on) void load()
  }
)

watch(
  () => [conn.transport, props.id] as const,
  ([t]) => {
    batch.value = null
    if (t) void load()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  alive = false
  clearTimeout(timer)
})

type Filter = 'all' | 'running' | 'awaiting_human' | 'succeeded' | 'failed' | 'cancelled'
const filter = ref<Filter>('all')
const MATCH: Record<Filter, (r: FlowBatchRun) => boolean> = {
  all: () => true,
  running: (r) => r.status === 'running' || r.status === 'queued',
  awaiting_human: (r) => r.status === 'awaiting_human',
  succeeded: (r) => r.status === 'succeeded',
  failed: (r) => r.status === 'failed',
  cancelled: (r) => r.status === 'cancelled'
}
const stats = computed(() => [
  { id: 'succeeded' as const, value: count('succeeded'), label: `/ ${total.value} xong`, tone: '' },
  { id: 'running' as const, value: count('running') + count('queued'), label: 'đang chạy / chờ', tone: 'going' },
  { id: 'awaiting_human' as const, value: count('awaiting_human'), label: 'chờ bạn duyệt', tone: 'human' },
  { id: 'failed' as const, value: count('failed'), label: 'lỗi', tone: 'bad' },
  { id: 'cancelled' as const, value: count('cancelled'), label: 'đã huỷ', tone: '' }
])
const shown = computed(() => (batch.value?.runs ?? []).map((r, i) => ({ r, i })).filter(({ r }) => MATCH[filter.value](r)))
const toggle = (f: Filter): void => void (filter.value = filter.value === f ? 'all' : f)

const flowTitle = computed(() => flows.byId(batch.value?.workflow_id ?? '')?.title ?? batch.value?.workflow_id ?? '')

async function act(kind: 'cancel' | 'retry'): Promise<void> {
  if (busy.value) return
  busy.value = kind
  try {
    const d = kind === 'cancel' ? await api().cancelFlowBatch(props.id) : await api().retryFailedFlowBatch(props.id)
    batch.value = d
    if (kind === 'retry') toasts.push(d.retried ? `Chạy lại ${d.retried} bước lỗi` : 'Không có bước lỗi để chạy lại', 'info', 2500)
    else toasts.push('Đã huỷ các lượt đang chạy', 'info', 2500)
    void load()
  } catch (e) {
    toasts.error(e, kind === 'cancel' ? 'Không huỷ được lô' : 'Không chạy lại được lô')
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="view">
    <PageHeader :title="batch?.title || 'Lô workflow'">
      <template #leading>
        <RouterLink to="/flows" class="btn btn-plain btn-icon" aria-label="Về danh sách workflow"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="batch" #subtitle>
        {{ flowTitle }} · <RelTime :ts="batch.created_at" /> · <span class="num">{{ total }}</span> lượt
      </template>
      <EtaChip v-if="batch" :batch="batch" />
      <button v-if="failed" class="btn" :disabled="!!busy" aria-label="Chạy lại lỗi" title="Chạy lại các lượt lỗi" @click="act('retry')">
        <LoaderCircle v-if="busy === 'retry'" class="spin" />
        <RotateCw v-else />
        <span class="lbl">Chạy lại lỗi</span>
      </button>
      <button v-if="active" class="btn btn-danger" :disabled="!!busy" aria-label="Huỷ lô" title="Huỷ lô" @click="act('cancel')">
        <LoaderCircle v-if="busy === 'cancel'" class="spin" />
        <CircleStop v-else />
        <span class="lbl">Huỷ lô</span>
      </button>
    </PageHeader>

    <EmptyState v-if="loadError && !batch" :icon="CircleAlert" title="Không mở được lô" :text="loadError" action-label="Về danh sách workflow" action-to="/flows">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>
    <p v-else-if="!batch" class="caption wait">Đang tải…</p>

    <div v-else class="scroll">
      <div class="stack">
        <section class="summary" aria-label="Lọc theo trạng thái">
          <button
            v-for="st in stats"
            :key="st.id"
            type="button"
            class="stat card"
            :class="{ on: filter === st.id }"
            :aria-pressed="filter === st.id"
            :disabled="!st.value && filter !== st.id"
            @click="toggle(st.id)"
          >
            <span class="big num" :class="st.value ? st.tone : 'zero'"><TweenNumber :value="st.value" /></span>
            <span class="caption">{{ st.label }}</span>
          </button>
        </section>

        <section class="grp">
          <div class="sec-head">
            <h2 class="section-label">Từng lượt</h2>
            <button v-if="filter !== 'all'" class="btn btn-plain small" @click="filter = 'all'">Bỏ lọc · xem cả <span class="num">{{ total }}</span></button>
          </div>
          <ul class="runs card">
            <li v-for="{ r, i } in shown" :key="r.id">
              <RouterLink :to="`/flows/runs/${r.id}`" class="run">
                <span class="idx num">{{ i + 1 }}</span>
                <div class="r-main">
                  <span class="r-title" :title="r.title">{{ r.title }}</span>
                  <span v-if="r.error" class="caption err" :title="r.error">{{ r.error }}</span>
                  <span v-else class="caption num">
                    {{ Object.values(r.nodes).filter((n) => n === 'succeeded').length }}/{{ Object.keys(r.nodes).length }} node
                  </span>
                </div>
                <FlowStatusBadge :status="r.status" />
                <ChevronRight :size="16" class="chev" aria-hidden="true" />
              </RouterLink>
            </li>
            <li v-if="!shown.length" class="none caption">Không có lượt nào ở trạng thái này.</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
.stat {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 14px 16px;
  border: 0;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    box-shadow var(--t-base) var(--ease-out),
    transform var(--t-fast) var(--ease-spring);
}
.stat:hover:not(:disabled) {
  box-shadow: var(--shadow-2), var(--hairline);
}
.stat:active:not(:disabled) {
  transform: scale(0.98);
}
.stat:disabled {
  cursor: default;
}
.stat.on {
  box-shadow: 0 0 0 2px var(--accent), var(--shadow-1);
}
.big {
  font-size: var(--fs-title1);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.run {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 12px;
  border-radius: var(--r-sm);
  color: inherit;
  text-decoration: none;
  transition: background var(--t-fast) var(--ease-out);
}
.run:hover {
  background: var(--bg-active);
}
.going {
  color: var(--st-running);
}
.human {
  color: var(--st-human);
}
.bad {
  color: var(--st-fail);
}
.zero {
  color: var(--text-3);
}
.grp {
  display: grid;
  gap: 10px;
}
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 24px;
}
.small {
  height: 24px;
  font-size: var(--fs-callout);
}
.runs {
  list-style: none;
  margin: 0;
  padding: 4px;
}
.idx {
  min-width: 28px;
  color: var(--text-3);
  text-align: right;
}
.r-main {
  flex: 1;
  min-width: 0;
  display: grid;
}
.r-title {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.err {
  color: var(--st-fail);
  overflow-wrap: anywhere;
}
.chev {
  color: var(--text-3);
}
.none {
  padding: 14px;
}
.wait {
  padding: 24px;
}
</style>
