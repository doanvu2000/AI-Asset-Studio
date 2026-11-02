<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChevronRight, Copy, Pencil, Play, Plus, RotateCw, Trash2, Waypoints } from '@lucide/vue'
import type { FlowBatchSummary, WorkflowDef, WorkflowRunRow } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import SectionTabs from '@/components/SectionTabs.vue'
import { RECIPE_TABS } from '@/composables/navTabs'
import EmptyState from '@/components/EmptyState.vue'
import FlowStatusBadge from '@/components/FlowStatusBadge.vue'
import RelTime from '@/components/RelTime.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { chainNames } from '@/composables/flows'
import { useFlows } from '@/stores/flows'
import { useRecipes } from '@/stores/recipes'
import { useConnection, api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const flows = useFlows()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

const batches = ref<FlowBatchSummary[]>([])
let batchTimer: ReturnType<typeof setTimeout> | undefined
let alive = true

watch(
  () => conn.transport,
  (t) => {
    if (!t) return
    void recipes.load()
    void flows.loadDefs()
    void flows.loadRuns()
    void loadBatches()
  },
  { immediate: true }
)

async function loadBatches(): Promise<void> {
  clearTimeout(batchTimer)
  try {
    batches.value = await api().flowBatches(8)
  } catch {
    // the list is optional chrome; the page works without it
  }
  // workflow.updated events drive the refresh while the socket is up; the timer only covers a dropped socket.
  if (alive && conn.socket !== 'open' && batches.value.some((b) => b.status === 'running'))
    batchTimer = setTimeout(() => void loadBatches(), 3000)
}
watch(
  () => flows.runList,
  () => {
    if (!batches.value.some((b) => b.status === 'running')) return
    clearTimeout(batchTimer)
    batchTimer = setTimeout(() => void loadBatches(), 400)
  }
)
watch(
  () => conn.socket,
  (s) => s !== 'open' && void loadBatches()
)
onBeforeUnmount(() => {
  alive = false
  clearTimeout(batchTimer)
})
const doneOf = (b: FlowBatchSummary): number => b.counts.succeeded ?? 0
const BATCH_LABEL: Record<FlowBatchSummary['status'], string> = {
  running: 'Đang chạy',
  awaiting_human: 'Chờ bạn duyệt',
  failed: 'Có lượt lỗi',
  succeeded: 'Xong',
  cancelled: 'Đã huỷ'
}

const SOURCE: Record<string, { label: string; tone: string } | undefined> = {
  user: { label: 'Của tôi', tone: 'user' },
  override: { label: 'Đã sửa', tone: 'edit' }
}
const sorted = computed(() => [...flows.defs].sort((a, b) => a.title.localeCompare(b.title, 'vi')))
const chain = (d: WorkflowDef): string[] => chainNames(d, recipes.byId)
const owned = (d: WorkflowDef): boolean => d.source === 'user' || d.source === 'override'

const titleOf = (r: WorkflowRunRow): string => r.title || flows.byId(r.workflow_id)?.title || 'Workflow đã xoá'
function progressOf(r: WorkflowRunRow): { done: number; total: number } {
  const nodes = Object.values(r.nodes)
  return { done: nodes.filter((n) => n.status === 'succeeded').length, total: nodes.length }
}
const recent = computed(() => flows.runList.slice(0, 12))

const confirming = ref<string | null>(null)
const deleting = ref(false)
async function remove(d: WorkflowDef): Promise<void> {
  if (confirming.value !== d.id) return void (confirming.value = d.id)
  deleting.value = true
  try {
    const res = await api().deleteFlow(d.id)
    toasts.push(res.restored_builtin ? 'Đã bỏ bản sửa, dùng lại bản gốc' : 'Đã xoá workflow', 'success', 2500)
    confirming.value = null
    await flows.loadDefs()
  } catch (e) {
    toasts.error(e, 'Không xoá được workflow')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Workflow" subtitle="Nối nhiều quy trình thành một chuỗi chạy tự động">
      <button class="btn btn-primary" @click="router.push('/flows/new')"><Plus /> Workflow mới</button>
    </PageHeader>
    <SectionTabs :tabs="RECIPE_TABS" current="/flows" label="Công thức" />
    <div class="scroll">
      <div class="stack stack-wide">
        <EmptyState
          v-if="(flows.defsLoaded || flows.defsError) && !flows.defs.length"
          :icon="Waypoints"
          :title="flows.defsError ? 'Không tải được workflow' : 'Chưa có workflow nào'"
          :text="flows.defsError ?? 'Tạo workflow để nối nhiều quy trình lại, ví dụ từ concept sang sprite 8 hướng.'"
          action-label="Workflow mới"
          action-to="/flows/new"
          :action-icon="Plus"
        >
          <button v-if="flows.defsError" class="btn" @click="flows.loadDefs()"><RotateCw /> Thử lại</button>
        </EmptyState>

        <div v-else class="grid">
          <article v-for="d in sorted" :key="d.id" class="wc card">
            <div class="wc-main">
              <div class="wc-top">
                <h2 class="headline name" :title="d.title">{{ d.title }}</h2>
                <span v-if="SOURCE[d.source ?? 'builtin']" class="src-badge" :class="SOURCE[d.source ?? 'builtin']!.tone">{{ SOURCE[d.source ?? 'builtin']!.label }}</span>
              </div>
              <p v-if="d.description" class="callout desc">{{ d.description }}</p>
              <p class="caption chain" :title="chain(d).join(' → ')">
                <span v-for="(n, i) in chain(d)" :key="i" class="link">
                  <span class="link-name">{{ n }}</span>
                  <ArrowRight v-if="i < chain(d).length - 1" :size="12" class="arr" aria-hidden="true" />
                </span>
              </p>
              <span class="caption num count">{{ d.nodes.length }} node · {{ Object.keys(d.inputs).length }} đầu vào</span>
            </div>
            <div class="wc-actions">
              <RouterLink :to="`/flows/${encodeURIComponent(d.id)}/run`" class="btn btn-primary"><Play /> Chạy</RouterLink>
              <RouterLink :to="`/flows/${encodeURIComponent(d.id)}/edit`" class="btn btn-plain"><Pencil /> Sửa</RouterLink>
              <RouterLink :to="{ path: '/flows/new', query: { from: d.id } }" class="btn btn-plain"><Copy /> Nhân bản</RouterLink>
              <template v-if="owned(d)">
                <button type="button" class="btn btn-plain danger push" :disabled="deleting" @click="remove(d)" @blur="confirming = null">
                  <Trash2 />
                  {{ confirming === d.id ? (d.source === 'override' ? 'Bỏ bản sửa?' : 'Xác nhận xoá?') : d.source === 'override' ? 'Bỏ bản sửa' : 'Xoá' }}
                </button>
              </template>
            </div>
          </article>
        </div>

        <section v-if="batches.length" class="recent" aria-labelledby="batch-h">
          <h2 id="batch-h" class="section-label">Lô hàng loạt gần đây</h2>
          <ul class="runs card">
            <li v-for="b in batches" :key="b.id">
              <RouterLink :to="`/flows/batches/${b.id}`" class="run">
                <div class="r-main">
                  <span class="r-title" :title="b.title">{{ b.title }}</span>
                  <span class="caption"><RelTime :ts="b.created_at" /></span>
                </div>
                <span class="caption num prog"><TweenNumber :value="doneOf(b)" :duration="200" />/{{ b.total }} lượt</span>
                <span class="caption">{{ BATCH_LABEL[b.status] }}</span>
                <ChevronRight :size="16" class="chev" aria-hidden="true" />
              </RouterLink>
            </li>
          </ul>
        </section>

        <section v-if="recent.length" class="recent" aria-labelledby="recent-h">
          <h2 id="recent-h" class="section-label">Lượt chạy gần đây</h2>
          <ul class="runs card">
            <li v-for="r in recent" :key="r.id">
              <RouterLink :to="`/flows/runs/${r.id}`" class="run">
                <div class="r-main">
                  <span class="r-title" :title="titleOf(r)">{{ titleOf(r) }}</span>
                  <span class="caption"><RelTime :ts="r.created_at" /></span>
                </div>
                <span class="caption num prog"><TweenNumber :value="progressOf(r).done" :duration="200" />/{{ progressOf(r).total }} node</span>
                <FlowStatusBadge :status="r.status" />
                <ChevronRight :size="16" class="chev" aria-hidden="true" />
              </RouterLink>
            </li>
          </ul>
        </section>
        <p v-else-if="flows.runsLoaded && flows.defs.length" class="caption">Chưa có lượt chạy workflow nào.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}
.wc {
  display: grid;
  grid-template-rows: 1fr auto;
  overflow: hidden;
  transition: box-shadow var(--t-base) var(--ease-out);
}
.wc:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.wc-main {
  display: grid;
  gap: 8px;
  align-content: start;
  padding: 16px 16px 12px;
}
.wc-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.name {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.desc {
  color: var(--text-2);
}
.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
}
.link-name {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-active);
  color: var(--text-2);
  font-weight: 600;
}
.arr {
  color: var(--text-3);
}
.link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.src-badge {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 650;
  color: var(--text-2);
  background: var(--bg-active);
}
.src-badge.user {
  color: var(--accent);
  background: var(--accent-soft);
}
.src-badge.edit {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.wc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 10px 10px;
  border-top: 0.5px solid var(--separator);
}
.wc-actions .btn {
  height: 28px;
  font-size: var(--fs-callout);
}
.push {
  margin-left: auto;
}
.danger {
  color: var(--danger);
}
.runs {
  list-style: none;
  margin: 8px 0 0;
  padding: 4px;
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
.r-main {
  flex: 1;
  min-width: 0;
  display: grid;
}
.r-title {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.prog {
  white-space: nowrap;
}
.chev {
  color: var(--text-3);
}
</style>
