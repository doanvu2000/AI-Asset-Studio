<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onKeyStroke } from '@vueuse/core'
import { ArrowLeft, CircleAlert, CircleStop, ExternalLink, FolderKanban, Hand, List, LoaderCircle, Recycle, RotateCcw, Workflow } from '@lucide/vue'
import { NO_PROJECT, WORKFLOW_ACTIVE, type WorkflowChild } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import FlowStatusBadge from '@/components/FlowStatusBadge.vue'
import ElapsedTimer from '@/components/ElapsedTimer.vue'
import EtaChip from '@/components/EtaChip.vue'
import StatusGlyph from '@/components/StatusGlyph.vue'
import BlobImage from '@/components/BlobImage.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import FlowCanvas from '@/components/flow/FlowCanvas.vue'
import { buildFlowRunGraph } from '@/composables/flowGraph'
import { NODE_GLYPH, NODE_LABEL } from '@/composables/flows'
import { absoluteTime, humanDuration } from '@/composables/format'
import { useFlows } from '@/stores/flows'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'
import { useProjects } from '@/stores/projects'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ id: string }>()
const flows = useFlows()
const recipes = useRecipes()
const runs = useRuns()
const projects = useProjects()
const conn = useConnection()
const toasts = useToasts()

const detail = computed(() => flows.details.get(props.id))
const gone = computed(() => flows.missing.has(props.id))
const loadError = ref<string | null>(null)
const active = computed(() => !!detail.value && WORKFLOW_ACTIVE.has(detail.value.status))

async function load(id: string): Promise<void> {
  try {
    await flows.loadDetail(id)
    loadError.value = null
  } catch (e) {
    loadError.value = (e as Error).message
  }
}
watch(
  [() => props.id, () => conn.transport],
  ([id, t], [prev]) => {
    if (prev && prev !== id) flows.closeDetail(prev)
    if (t) {
      void recipes.load()
      void load(id)
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  flows.closeDetail(props.id)
  if (watchedRun) runs.closeDetail(watchedRun, 'flow')
})

const MIN_GRAPH_WIDTH = 1100
const MODE_KEY = 'studio.flow.view'
function readMode(): 'graph' | 'list' {
  try {
    const m = localStorage.getItem(MODE_KEY)
    if (m === 'graph' || m === 'list') return m
  } catch {
    /* storage unavailable */
  }
  return window.innerWidth < MIN_GRAPH_WIDTH ? 'list' : 'graph'
}
const viewMode = ref<'graph' | 'list'>(readMode())
function setMode(m: 'graph' | 'list'): void {
  viewMode.value = m
  try {
    localStorage.setItem(MODE_KEY, m)
  } catch {
    /* storage unavailable */
  }
}

const children = computed(() => detail.value?.children ?? [])
const manualId = ref<string | null>(null)
const currentId = computed<string | null>(() => {
  const list = children.value
  if (manualId.value && list.some((c) => c.node_id === manualId.value)) return manualId.value
  const pick = (s: string): WorkflowChild | undefined => list.find((c) => c.status === s)
  return (pick('running') ?? pick('awaiting_human') ?? pick('failed') ?? [...list].reverse().find((c) => c.status === 'succeeded') ?? list[0])?.node_id ?? null
})
watch(
  () => props.id,
  () => (manualId.value = null)
)

const graph = computed(() =>
  buildFlowRunGraph(detail.value?.def_snapshot ?? {}, children.value, recipes.byId, currentId.value)
)
const ordered = computed(() => {
  const byId = new Map(children.value.map((c) => [c.node_id, c]))
  return graph.value.order.map((id) => byId.get(id)).filter((c): c is WorkflowChild => !!c)
})
const sel = computed(() => children.value.find((c) => c.node_id === currentId.value) ?? null)
const selOrder = computed(() => graph.value.order.indexOf(currentId.value ?? '') + 1)
const fitKey = computed(() => `${props.id}:${children.value.length}:${viewMode.value}`)

function select(id: string): void {
  manualId.value = id
}
function stepBy(delta: number, e: KeyboardEvent): void {
  const t = e.target as HTMLElement | null
  if (t?.closest('input, textarea, select, [contenteditable="true"]')) return
  const list = graph.value.order
  if (!list.length) return
  const i = list.indexOf(currentId.value ?? '')
  const next = list[Math.min(list.length - 1, Math.max(0, (i < 0 ? 0 : i) + delta))]
  if (!next) return
  e.preventDefault()
  manualId.value = next
}
onKeyStroke('ArrowRight', (e) => stepBy(1, e))
onKeyStroke('ArrowLeft', (e) => stepBy(-1, e))

let watchedRun: string | null = null
watch(
  () => sel.value?.run_id ?? null,
  (rid) => {
    if (watchedRun) runs.closeDetail(watchedRun, 'flow')
    watchedRun = rid
    if (rid) void runs.loadDetail(rid, 'flow').catch(() => undefined)
  },
  { immediate: true }
)
const childSteps = computed(() => (sel.value?.run_id ? runs.stepsOf(sel.value.run_id) : []))
const cachedCount = computed(() => childSteps.value.filter((s) => s.status === 'cached' || s.meta.cached_from).length)
const gateStep = computed(() => childSteps.value.find((s) => s.status === 'awaiting_human'))

const recipeLabel = (c: WorkflowChild): string => recipes.byId(c.recipe_id)?.name ?? (recipes.loaded ? `${c.recipe_id} (đã xoá)` : c.recipe_id)
const project = computed(() => {
  const id = detail.value?.project_id
  return id && id !== NO_PROJECT ? { id, name: projects.byId(id)?.name ?? 'Dự án' } : null
})
watch(
  () => project.value?.id,
  (id) => id && !projects.byId(id) && conn.transport && void projects.refresh(id).catch(() => undefined),
  { immediate: true }
)

const doneCount = computed(() => children.value.filter((c) => c.status === 'succeeded').length)
const retrying = ref(false)
async function retry(c: WorkflowChild): Promise<void> {
  retrying.value = true
  try {
    await flows.retryNode(props.id, c.node_id)
  } catch (e) {
    toasts.error(e, 'Không chạy lại được bước này')
  } finally {
    retrying.value = false
  }
}
const confirmCancel = ref(false)
const cancelling = ref(false)
async function cancel(): Promise<void> {
  cancelling.value = true
  try {
    await flows.cancel(props.id)
    confirmCancel.value = false
  } catch (e) {
    toasts.error(e, 'Không huỷ được workflow')
  } finally {
    cancelling.value = false
  }
}
</script>

<template>
  <div class="view">
    <PageHeader :title="detail?.title || 'Lượt chạy workflow'">
      <template #leading>
        <RouterLink to="/flows" class="btn btn-plain btn-icon" aria-label="Về danh sách workflow"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="detail" #subtitle>
        {{ absoluteTime(detail.created_at) }} ·
        <ElapsedTimer v-if="active" :from="detail.created_at" />
        <span v-else-if="detail.ended_at" class="num">{{ humanDuration(detail.ended_at - detail.created_at) }}</span>
      </template>
      <RouterLink v-if="project" :to="`/projects/${project.id}`" class="proj-badge" :title="`Dự án ${project.name}`">
        <FolderKanban :size="13" aria-hidden="true" />
        <span class="pb-name lbl">{{ project.name }}</span>
      </RouterLink>
      <div class="seg" role="radiogroup" aria-label="Kiểu xem">
        <button type="button" role="radio" :aria-checked="viewMode === 'graph'" :class="{ on: viewMode === 'graph' }" title="Sơ đồ" aria-label="Sơ đồ" @click="setMode('graph')"><Workflow :size="14" /> <span class="lbl">Sơ đồ</span></button>
        <button type="button" role="radio" :aria-checked="viewMode === 'list'" :class="{ on: viewMode === 'list' }" title="Danh sách" aria-label="Danh sách" @click="setMode('list')"><List :size="14" /> <span class="lbl">Danh sách</span></button>
      </div>
      <EtaChip v-if="detail" :flow="detail" />
      <FlowStatusBadge v-if="detail" :status="detail.status" />
      <template v-if="active">
        <button v-if="!confirmCancel" type="button" class="btn" aria-label="Huỷ workflow" title="Huỷ workflow" @click="confirmCancel = true"><CircleStop /> <span class="lbl">Huỷ workflow</span></button>
        <template v-else>
          <button type="button" class="btn" @click="confirmCancel = false">Không</button>
          <button type="button" class="btn btn-danger" :disabled="cancelling" @click="cancel">
            <LoaderCircle v-if="cancelling" class="spin" />
            Huỷ hẳn
          </button>
        </template>
      </template>
    </PageHeader>

    <div v-if="children.length" class="runbar" role="progressbar" :aria-valuemin="0" :aria-valuemax="children.length" :aria-valuenow="doneCount">
      <div class="segs">
        <span v-for="c in children" :key="c.node_id" class="sg" :class="`sg-${c.status}`" :title="`${c.title}: ${NODE_LABEL[c.status]}`" />
      </div>
      <span class="runbar-label caption num"><TweenNumber :value="doneCount" :duration="200" />/{{ children.length }} node</span>
    </div>
    <p v-if="detail?.error" class="note-err" role="alert"><CircleAlert :size="16" /> {{ detail.error }}</p>

    <EmptyState v-if="gone" :icon="CircleAlert" title="Lượt chạy này không còn" text="Lượt chạy đã bị xoá hoặc core đã được dọn dữ liệu.">
      <RouterLink to="/flows" class="btn">Về danh sách workflow</RouterLink>
    </EmptyState>
    <EmptyState v-else-if="loadError && !detail" :icon="CircleAlert" title="Không mở được lượt chạy" :text="loadError">
      <button class="btn" @click="load(props.id)">Thử lại</button>
    </EmptyState>
    <div v-else-if="detail" class="detail">
      <section class="rail" :class="{ graph: viewMode === 'graph' }" aria-label="Các node của workflow">
        <FlowCanvas
          v-if="viewMode === 'graph' && children.length"
          :id="`flow-${id}`"
          :nodes="graph.nodes"
          :edges="graph.edges"
          :fit-key="fitKey"
          :focus-id="currentId"
          :min-fit-zoom="0.75"
          scroll-pan
          :minimap="children.length > 6"
          @node-click="select"
        />
        <ol v-else-if="viewMode === 'list'" class="list scroll">
          <li v-for="(c, i) in ordered" :key="c.node_id">
            <button type="button" class="item" :class="[`st-${c.status}`, { on: c.node_id === currentId }]" @click="select(c.node_id)">
              <StatusGlyph :status="NODE_GLYPH[c.status]" :progress="c.status === 'succeeded' ? 1 : c.progress" :size="26" />
              <span class="ord num">{{ i + 1 }}</span>
              <span class="i-text">
                <span class="i-title">{{ c.title }}</span>
                <span class="caption">{{ recipeLabel(c) }} · {{ NODE_LABEL[c.status] }}</span>
              </span>
              <span v-if="c.preview" class="i-thumb"><BlobImage :hash="c.preview" :size="96" /></span>
            </button>
          </li>
        </ol>
        <p v-else class="caption pad">Workflow này chưa có node nào.</p>
      </section>

      <aside v-if="sel" class="inspector scroll" aria-label="Chi tiết node">
        <header class="p-head">
          <StatusGlyph :status="NODE_GLYPH[sel.status]" :progress="sel.status === 'succeeded' ? 1 : sel.progress" :size="30" />
          <div class="p-t">
            <h2 class="title-2">{{ selOrder }}. {{ sel.title }}</h2>
            <p class="caption">{{ recipeLabel(sel) }}</p>
          </div>
        </header>

        <dl class="facts">
          <div>
            <dt>Trạng thái</dt>
            <dd :class="`tone-${sel.status}`">{{ NODE_LABEL[sel.status] }}</dd>
          </div>
          <div>
            <dt>Số lần thử</dt>
            <dd class="num">{{ sel.attempts }}</dd>
          </div>
          <div v-if="sel.status === 'running' && sel.progress !== null">
            <dt>Tiến độ</dt>
            <dd class="num"><TweenNumber :value="sel.progress * 100" />%</dd>
          </div>
        </dl>
        <div v-if="sel.status === 'running'" class="bar" :class="{ indet: sel.progress === null }">
          <span class="fill" :style="{ transform: `scaleX(${sel.progress ?? 0.3})` }" />
        </div>
        <span v-if="cachedCount" class="chip-cache" :title="`${cachedCount} bước của node này lấy lại kết quả đã có, không chạy lại`"><Recycle :size="13" /> Dùng lại {{ cachedCount }} bước</span>

        <p v-if="sel.error" class="err selectable" role="alert"><CircleAlert :size="15" /> {{ sel.error }}</p>
        <p v-else-if="sel.status === 'blocked'" class="caption">Bước trước đó lỗi hoặc bị huỷ nên node này chưa chạy.</p>

        <div v-if="sel.preview" class="shot checker"><BlobImage :hash="sel.preview" :size="512" /></div>

        <div class="p-actions">
          <RouterLink v-if="gateStep" :to="{ path: '/gates', query: { step: gateStep.id } }" class="btn btn-primary"><Hand /> Duyệt ngay</RouterLink>
          <RouterLink v-if="sel.run_id" :to="`/runs/${sel.run_id}`" class="btn"><ExternalLink /> Mở lượt chạy</RouterLink>
          <button v-if="sel.status === 'failed' || sel.status === 'cancelled'" type="button" class="btn" :disabled="retrying" @click="retry(sel)">
            <LoaderCircle v-if="retrying" class="spin" />
            <RotateCcw v-else />
            Chạy lại bước này
          </button>
        </div>
        <p class="caption hint">Dùng phím ← → để chuyển node. Cuộn chuột để di chuyển sơ đồ, Ctrl + cuộn để phóng to.</p>
      </aside>
    </div>
    <p v-else class="caption pad">Đang tải…</p>
  </div>
</template>

<style scoped>
.view {
  height: 100%;
}
.detail {
  flex: 1;
  min-height: 0;
  display: flex;
}
.rail {
  flex: 1;
  min-width: 0;
  position: relative;
  border-right: 0.5px solid var(--separator);
}
.rail:not(.graph) {
  max-width: 560px;
}
.inspector {
  width: 380px;
  flex: none;
  display: grid;
  gap: 14px;
  align-content: start;
}
.pad {
  padding: var(--sp-6);
}
.seg {
  display: inline-flex;
  flex: none;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-callout);
  cursor: pointer;
}
.seg button.on {
  background: var(--bg-elevated);
  color: var(--text);
  font-weight: 600;
  box-shadow: var(--shadow-1);
}
.proj-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 200px;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  text-decoration: none;
}
.pb-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px var(--sp-6);
  border-bottom: 0.5px solid var(--separator);
}
.segs {
  flex: 1;
  display: flex;
  gap: 3px;
  min-width: 0;
}
.sg {
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: var(--separator-strong);
  transition: background-color var(--t-base) var(--ease-out);
}
.sg-succeeded {
  background: var(--st-ok);
}
.sg-running {
  background: var(--st-running);
  animation: soft-pulse 1.6s var(--ease-out) infinite;
}
.sg-awaiting_human {
  background: var(--st-human);
}
.sg-failed {
  background: var(--st-fail);
}
.sg-blocked {
  background: var(--st-waiting);
  opacity: 0.5;
}
.sg-cancelled {
  background: repeating-linear-gradient(90deg, var(--separator-strong) 0 4px, transparent 4px 7px);
}
.runbar-label {
  flex: none;
  min-width: 9ch;
  text-align: right;
}
.note-err {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 8px var(--sp-6);
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.list {
  list-style: none;
  margin: 0;
  display: grid;
  gap: 8px;
  align-content: start;
  height: 100%;
}
.item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--hairline);
  text-align: left;
  cursor: pointer;
  transition: box-shadow var(--t-base) var(--ease-out);
}
.item.on {
  box-shadow: 0 0 0 2px var(--accent);
}
.item.st-pending,
.item.st-blocked {
  opacity: 0.75;
}
.ord {
  flex: none;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  display: inline-grid;
  place-items: center;
  border-radius: 11px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-2);
  background: var(--bg-sunken);
}
.i-text {
  flex: 1;
  min-width: 0;
  display: grid;
}
.i-title {
  font-weight: 700;
  font-size: var(--fs-body);
  overflow-wrap: anywhere;
}
.i-thumb {
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
}
.p-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.p-t {
  min-width: 0;
}
.p-t h2 {
  overflow-wrap: anywhere;
}
.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 10px;
  margin: 0;
}
.facts dt {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.facts dd {
  margin: 2px 0 0;
  font-weight: 650;
}
.tone-running {
  color: var(--st-running);
}
.tone-awaiting_human {
  color: var(--st-human);
}
.tone-succeeded {
  color: var(--st-ok);
}
.tone-failed {
  color: var(--st-fail);
}
.tone-blocked {
  color: var(--st-waiting);
}
.bar {
  height: 5px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--st-running-soft);
}
.fill {
  display: block;
  height: 100%;
  background: var(--st-running);
  transform-origin: left;
  transition: transform 400ms var(--ease-out);
}
.bar.indet .fill {
  animation: panel-indet 1.4s var(--ease-out) infinite;
}
@keyframes panel-indet {
  from {
    transform: translateX(-100%) scaleX(0.3);
  }
  to {
    transform: translateX(340%) scaleX(0.3);
  }
}
.chip-cache {
  justify-self: start;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  color: var(--st-cached);
  background: var(--st-cached-soft);
}
.err {
  display: flex;
  gap: 8px;
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--r-md);
  color: var(--st-fail);
  background: var(--st-fail-soft);
  overflow-wrap: anywhere;
}
.err svg {
  flex: none;
  margin-top: 2px;
}
.shot {
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.p-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hint {
  margin: 0;
}
</style>
