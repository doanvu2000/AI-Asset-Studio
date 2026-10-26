<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, CircleAlert, CircleStop, Inbox } from '@lucide/vue'
import type { RunRow, StepRow } from '@/api/types'
import { RUN_ACTIVE } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusGlyph from '@/components/StatusGlyph.vue'
import RunStatusBadge from '@/components/RunStatusBadge.vue'
import LaneTag from '@/components/LaneTag.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import { useBatches } from '@/stores/batches'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useGates } from '@/stores/gates'
import { kindName, STEP_LABEL, stepLabel } from '@/composables/format'
import { settlePool } from '@/composables/pool'

const props = defineProps<{ id: string }>()
const batches = useBatches()
const runs = useRuns()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const gates = useGates()
const router = useRouter()

const batch = computed(() => batches.byId(props.id))
const HOLDER = 'batch'
const held = new Set<string>()

watch(
  () => [conn.transport, props.id] as const,
  ([t, id]) => {
    if (t) void runs.loadBatch(id).catch(() => undefined)
  },
  { immediate: true }
)

function releaseAll(): void {
  for (const id of held) runs.closeDetail(id, HOLDER)
  held.clear()
}
watch(() => props.id, releaseAll)

const DETAIL_CONCURRENCY = 6
watch(
  () => [conn.transport, batch.value?.runs.map((r) => r.id).join(',')] as const,
  ([t]) => {
    if (!t || !batch.value) return
    const fresh = batch.value.runs.map((r) => r.id).filter((id) => !held.has(id))
    for (const id of fresh) held.add(id)
    void settlePool(fresh, DETAIL_CONCURRENCY, async (id) => {
      try {
        await runs.loadDetail(id, HOLDER)
      } catch {
        runs.closeDetail(id, HOLDER)
        held.delete(id)
      }
    })
  },
  { immediate: true }
)
onBeforeUnmount(releaseAll)

const stages = computed(() => (batch.value ? batches.stages(batch.value) : []))
const total = computed(() => batch.value?.runs.length ?? 0)
const stepFor = (runId: string, key: string): StepRow | undefined => {
  return runs.stepsOf(runId).find((s) => s.key === key)
}
const fraction = (s: StepRow | undefined): number | null => {
  if (!s) return null
  const p = runs.progress.get(s.id)
  return p && p.max ? Math.min(1, p.value / p.max) : null
}
const currentStage = computed(() => stages.value.find((c) => c.done < c.total)?.key ?? null)

type Filter = 'all' | 'done' | 'running' | 'queued' | 'awaiting' | 'failed'
const filter = ref<Filter>('all')
const isQueued = (r: RunRow): boolean =>
  r.status === 'running' &&
  !runs.stepsOf(r.id).some((s) => s.status === 'running') &&
  runs.stepsOf(r.id).some((s) => s.status === 'waiting_resource')
const MATCH: Record<Filter, (r: RunRow) => boolean> = {
  all: () => true,
  done: (r) => r.status === 'succeeded',
  running: (r) => r.status === 'running' && !isQueued(r),
  queued: isQueued,
  awaiting: (r) => r.status === 'awaiting_human',
  failed: (r) => r.status === 'failed' || r.status === 'interrupted' || r.status === 'cancelled'
}
const shownRuns = computed(() => {
  const all = batch.value?.runs ?? []
  return all.map((r, i) => ({ r, i })).filter(({ r }) => MATCH[filter.value](r))
})
function toggleFilter(f: Filter): void {
  filter.value = filter.value === f ? 'all' : f
}

const stats = computed(() => {
  const b = batch.value
  if (!b) return []
  return [
    { id: 'done' as const, value: b.done, label: `/ ${total.value} hoàn tất`, tone: '' },
    { id: 'running' as const, value: b.running, label: 'đang chạy', tone: 'run' },
    { id: 'queued' as const, value: b.queued, label: 'chờ GPU', tone: 'wait' },
    { id: 'awaiting' as const, value: b.awaiting, label: 'chờ bạn duyệt', tone: 'human' },
    { id: 'failed' as const, value: b.failed, label: 'lỗi / huỷ', tone: 'bad' }
  ]
})

// Opens the gate inbox at this batch's oldest pending gate; `batch` lets the inbox keep to this batch.
function reviewBatch(): void {
  const ids = new Set(batch.value?.runs.map((r) => r.id))
  const first = gates.list.find((g) => ids.has(g.runId))
  void router.push({ path: '/gates', query: { ...(first ? { step: first.stepId } : {}), batch: props.id } })
}

async function cancelAll(): Promise<void> {
  const active = batch.value?.runs.filter((r) => RUN_ACTIVE.has(r.status)) ?? []
  const res = await Promise.allSettled(active.map((r) => runs.cancel(r.id)))
  const failed = res.filter((r) => r.status === 'rejected').length
  if (failed) toasts.push(`${failed} lượt không huỷ được`, 'error')
  else toasts.push(`Đã huỷ ${active.length} lượt`, 'info')
}
</script>

<template>
  <div class="view">
    <PageHeader :title="batch?.title || 'Lô chạy'">
      <template #leading>
        <RouterLink to="/batch" class="btn btn-plain btn-icon" aria-label="Về danh sách lô"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="batch" #subtitle>
        {{ recipes.byId(batch.recipeId)?.name ?? 'Quy trình đã xoá' }} · <RelTime :ts="batch.createdAt" /> · <span class="num">{{ total }}</span> lượt
      </template>
      <button v-if="batch?.active" class="btn btn-danger" @click="cancelAll"><CircleStop /> Huỷ các lượt đang chạy</button>
      <button v-if="batch?.awaiting" class="btn btn-primary" @click="reviewBatch"><Inbox /> Duyệt cả lô</button>
    </PageHeader>

    <EmptyState
      v-if="!batch"
      :icon="CircleAlert"
      title="Không thấy lô này"
      text="Lô có thể đã cũ hơn danh sách lượt chạy đang tải."
      action-label="Về danh sách lô"
      action-to="/batch"
    />

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
            @click="toggleFilter(st.id)"
          >
            <span class="big num" :class="st.value ? st.tone : 'zero'"><TweenNumber :value="st.value" /></span>
            <span class="caption">{{ st.label }}</span>
          </button>
        </section>

        <section class="grp">
          <h2 class="section-label">Theo công đoạn</h2>
          <p class="caption">GPU ưu tiên chạy hết một công đoạn cho cả lô rồi mới sang công đoạn sau, đỡ nạp lại model.</p>
          <ol class="stages">
            <li v-for="c in stages" :key="c.key" class="stage card" :class="{ now: c.key === currentStage, full: c.done === c.total }">
              <div class="st-top">
                <span class="st-name" :title="kindName(c.kind)">{{ stepLabel(c.kind, c.key) }}</span>
                <LaneTag :lane="c.lane" />
              </div>
              <div class="st-bar">
                <span class="fill" :style="{ transform: `scaleX(${c.total ? c.done / c.total : 0})` }" />
                <span class="fill live" :style="{ transform: `scaleX(${c.total ? c.running / c.total : 0})`, left: `${c.total ? (c.done / c.total) * 100 : 0}%` }" />
              </div>
              <span class="st-count num"><TweenNumber :value="c.done" :duration="200" />/{{ c.total }}</span>
            </li>
          </ol>
        </section>

        <section class="grp">
          <div class="sec-head">
            <h2 class="section-label">Từng lượt</h2>
            <button v-if="filter !== 'all'" class="btn btn-plain small" @click="filter = 'all'">Bỏ lọc · xem cả <span class="num">{{ total }}</span></button>
          </div>
          <div class="matrix card" :style="{ '--cols': stages.length || 1 }" role="table" aria-label="Trạng thái từng bước của từng lượt">
            <div class="mrow head" role="row">
              <span class="mcell mtitle" role="columnheader">Lượt</span>
              <span v-for="c in stages" :key="c.key" class="mcell" role="columnheader" :title="kindName(c.kind)">{{ stepLabel(c.kind, c.key) }}</span>
              <span class="mcell st" role="columnheader">Trạng thái</span>
            </div>
            <RouterLink v-for="{ r, i } in shownRuns" :key="r.id" :to="`/runs/${r.id}`" class="mrow" role="row">
              <span class="mcell mtitle" role="cell"><span class="idx num">{{ i + 1 }}</span>{{ r.title || `Lượt ${i + 1}` }}</span>
              <span v-for="c in stages" :key="c.key" class="mcell" role="cell" :title="stepFor(r.id, c.key) ? STEP_LABEL[stepFor(r.id, c.key)!.status] : ''">
                <StatusGlyph v-if="stepFor(r.id, c.key)" :status="stepFor(r.id, c.key)!.status" :progress="fraction(stepFor(r.id, c.key))" :size="18" />
              </span>
              <span class="mcell st" role="cell"><RunStatusBadge :status="r.status" /></span>
            </RouterLink>
            <p v-if="!shownRuns.length" class="none caption">Không có lượt nào ở trạng thái này.</p>
          </div>
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
.wait {
  color: var(--st-waiting);
}
.zero {
  color: var(--text-3);
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
.none {
  padding: 14px;
}
.run {
  color: var(--st-running);
}
.human {
  color: var(--st-human);
}
.bad {
  color: var(--st-fail);
}
.grp {
  display: grid;
  gap: 10px;
}
.stages {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 10px;
}
.stage {
  position: relative;
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  transition: box-shadow var(--t-base) var(--ease-out);
}
.stage.now {
  box-shadow: 0 0 0 2px var(--st-running), var(--shadow-2);
}
.st-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.st-name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.st-bar {
  position: relative;
  height: 6px;
  margin-top: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-active);
}
.fill {
  position: absolute;
  inset: 0 auto 0 0;
  width: 100%;
  transform-origin: left;
  background: var(--st-ok);
  transition: transform 420ms var(--ease-out);
}
.fill.live {
  background: var(--st-running);
  opacity: 0.55;
}
.st-count {
  justify-self: end;
  font-size: var(--fs-callout);
  font-weight: 600;
  color: var(--text-2);
}
.full .st-count {
  color: var(--st-ok);
}
.matrix {
  overflow: auto;
  padding: 4px 0;
}
.mrow {
  display: grid;
  grid-template-columns: minmax(200px, 2fr) repeat(var(--cols, 1), minmax(56px, 1fr)) 128px;
  align-items: center;
  min-height: 40px;
  padding: 0 14px;
  color: inherit;
  text-decoration: none;
  transition: background-color var(--t-fast);
}
a.mrow:hover {
  background: var(--bg-hover);
}
.mrow + .mrow {
  border-top: 0.5px solid var(--separator);
}
.mrow.head {
  min-height: 32px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
}
.mcell {
  display: flex;
  justify-content: center;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcell.mtitle {
  justify-content: flex-start;
  gap: 10px;
  font-weight: 500;
  font-family: var(--font);
}
.mcell.st {
  justify-content: flex-end;
  font-family: var(--font);
}
.idx {
  width: 22px;
  color: var(--text-3);
  font-size: var(--fs-caption);
}
</style>
