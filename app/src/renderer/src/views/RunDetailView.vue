<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, CircleAlert, CircleStop, Hand, Hourglass, Inbox, List, RotateCcw, Workflow } from '@lucide/vue'
import type { BlobRef, StepRow } from '@/api/types'
import { RUN_ACTIVE, STEP_DONE_OK } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import RunStatusBadge from '@/components/RunStatusBadge.vue'
import StepChip from '@/components/StepChip.vue'
import ElapsedTimer from '@/components/ElapsedTimer.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import AssetViewer from '@/components/viewers/AssetViewer.vue'
import { featuredAsset } from '@/components/viewers/detect'
import Lightbox from '@/components/Lightbox.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import EmptyState from '@/components/EmptyState.vue'
import FlowCanvas from '@/components/flow/FlowCanvas.vue'
import { buildRunGraph } from '@/composables/graph'
import { useKinds } from '@/stores/catalog'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { absoluteTime, humanDuration, kindName, STEP_LABEL, waitingHint, waitingLabel } from '@/composables/format'

const props = defineProps<{ id: string }>()
const runs = useRuns()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const kinds = useKinds()

const MODE_KEY = 'studio.run.view'
function readMode(): 'list' | 'graph' {
  try {
    return localStorage.getItem(MODE_KEY) === 'graph' ? 'graph' : 'list'
  } catch {
    return 'list'
  }
}
const viewMode = ref<'list' | 'graph'>(readMode())
watch(viewMode, (m) => {
  try {
    localStorage.setItem(MODE_KEY, m)
  } catch {
    /* storage unavailable */
  }
})

const loading = ref(true)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  try {
    await runs.loadDetail(props.id)
    loadError.value = null
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(
  () => conn.transport,
  (t) => {
    if (!t) return
    void load()
    void kinds.load()
  },
  { immediate: true }
)
onBeforeUnmount(() => runs.closeDetail(props.id))

const run = computed(() => runs.runs.get(props.id))
const steps = computed(() => {
  void runs.steps
  return runs.stepsOf(props.id)
})
const active = computed(() => !!run.value && RUN_ACTIVE.has(run.value.status))
const recipeName = computed(() => (run.value ? (recipes.byId(run.value.recipe_id)?.name ?? run.value.recipe_id) : ''))

const manualId = ref<string | null>(null)
const focusStep = computed<StepRow | undefined>(() => {
  const s = steps.value
  return (
    s.find((x) => x.status === 'failed') ??
    s.find((x) => x.status === 'awaiting_human') ??
    s.find((x) => x.status === 'running') ??
    s.find((x) => x.status === 'waiting_resource' || x.status === 'ready') ??
    [...s].reverse().find((x) => STEP_DONE_OK.has(x.status) && x.status !== 'skipped') ??
    s[0]
  )
})
const selected = computed(() => steps.value.find((s) => s.id === manualId.value) ?? focusStep.value)

function select(s: StepRow): void {
  manualId.value = manualId.value === s.id ? null : s.id
}

const retrying = ref<string | null>(null)
async function retry(s: StepRow): Promise<void> {
  retrying.value = s.id
  try {
    await runs.retry(s.id)
    manualId.value = null
  } catch (e) {
    toasts.error(e, 'Không thử lại được')
  } finally {
    retrying.value = null
  }
}

const confirmCancel = ref(false)
let confirmTimer: number | undefined
async function cancel(): Promise<void> {
  if (!confirmCancel.value) {
    confirmCancel.value = true
    confirmTimer = window.setTimeout(() => (confirmCancel.value = false), 3000)
    return
  }
  window.clearTimeout(confirmTimer)
  confirmCancel.value = false
  try {
    await runs.cancel(props.id)
    toasts.push('Đã huỷ run', 'info')
  } catch (e) {
    toasts.error(e, 'Không huỷ được')
  }
}

const sel = computed(() => selected.value)
const selProgress = computed(() => (sel.value ? runs.progress.get(sel.value.id) : undefined))
const selFraction = computed(() => {
  const p = selProgress.value
  return p && p.max ? Math.min(1, p.value / p.max) : null
})
const selPreview = computed(() => (sel.value ? runs.previews.get(sel.value.id) : undefined))
const selLogs = computed(() => (sel.value ? (runs.logs.get(sel.value.id) ?? []).slice(-120) : []))
const selOutputs = computed(() => (sel.value ? Object.entries(sel.value.outputs).filter(([, v]) => v.length) : []))
const selInputs = computed(() => (sel.value ? Object.entries(sel.value.inputs).filter(([, v]) => v.length) : []))
const selParams = computed(() => (sel.value && Object.keys(sel.value.params).length ? JSON.stringify(sel.value.params, null, 2) : ''))

const graph = computed(() => buildRunGraph(steps.value, kinds.byKind, selected.value?.id ?? null))
const graphFit = computed(() => `${props.id}:${steps.value.length}:${viewMode.value}`)
function selectByKey(key: string): void {
  const s = steps.value.find((x) => x.key === key)
  if (s) select(s)
}

const lightbox = ref<BlobRef | null>(null)
const selBlobs = computed(() => (sel.value ? [...Object.values(sel.value.outputs), ...Object.values(sel.value.inputs)].flat() : []))
const selFeatured = computed(() => {
  const s = sel.value
  if (!s) return null
  if (STEP_DONE_OK.has(s.status)) return featuredAsset(Object.values(s.outputs).flat())
  return s.status === 'awaiting_human' ? featuredAsset(Object.values(s.inputs).flat()) : null
})
</script>

<template>
  <div class="view">
    <PageHeader :title="run?.title || recipeName || 'Run'">
      <template #leading>
        <RouterLink to="/runs" class="btn btn-plain btn-icon" aria-label="Về danh sách run"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="run" #subtitle>
        {{ recipeName }} · {{ absoluteTime(run.created_at) }} ·
        <ElapsedTimer v-if="active" :from="run.created_at" />
        <span v-else-if="run.ended_at" class="num">{{ humanDuration(run.ended_at - run.created_at) }}</span>
      </template>
      <div class="seg" role="radiogroup" aria-label="Kiểu xem">
        <button type="button" role="radio" :aria-checked="viewMode === 'list'" :class="{ on: viewMode === 'list' }" title="Danh sách bước" @click="viewMode = 'list'"><List :size="14" /> Danh sách</button>
        <button type="button" role="radio" :aria-checked="viewMode === 'graph'" :class="{ on: viewMode === 'graph' }" title="Sơ đồ bước" @click="viewMode = 'graph'"><Workflow :size="14" /> Sơ đồ</button>
      </div>
      <RunStatusBadge v-if="run" :status="run.status" />
      <button v-if="active" class="btn" :class="{ 'btn-danger': confirmCancel }" @click="cancel">
        <CircleStop />
        {{ confirmCancel ? 'Bấm lần nữa để huỷ' : 'Huỷ run' }}
      </button>
    </PageHeader>

    <EmptyState v-if="loadError && !run" :icon="CircleAlert" title="Không mở được run" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>

    <div v-else class="detail">
      <section v-if="viewMode === 'graph'" class="rail graph" aria-label="Sơ đồ các bước">
        <FlowCanvas
          v-if="steps.length"
          :id="`run-${id}`"
          :nodes="graph.nodes"
          :edges="graph.edges"
          :fit-key="graphFit"
          :minimap="steps.length > 6"
          @node-click="selectByKey"
        />
      </section>
      <section v-else class="rail scroll" aria-label="Các bước">
        <Transition name="fade">
          <div v-if="run?.status === 'failed' || run?.status === 'interrupted'" class="run-err">
            <CircleAlert :size="16" />
            <span>
              {{ run.status === 'interrupted' ? 'Run bị gián đoạn khi core khởi động lại.' : 'Run dừng vì một bước lỗi.' }}
              Bấm <strong>Thử lại</strong> ở bước đó để chạy tiếp.
            </span>
          </div>
        </Transition>
        <ol v-if="steps.length" class="steps">
          <li
            v-for="s in steps"
            :key="s.id"
            class="rail-item"
            :class="{ done: STEP_DONE_OK.has(s.status) }"
          >
            <StepChip
              :step="s"
              :progress="runs.progress.get(s.id)"
              :selected="selected?.id === s.id"
              :retrying="retrying === s.id"
              @select="select(s)"
              @retry="retry(s)"
            />
          </li>
        </ol>
        <div v-else-if="loading" class="skel-list">
          <div v-for="i in 4" :key="i" class="skel" />
        </div>
      </section>

      <section v-if="sel" class="inspector scroll" aria-live="polite">
        <Transition name="swap" mode="out-in">
          <div :key="sel.id" class="insp">
            <header class="insp-head">
              <h2 class="title">{{ kindName(sel.kind) }}</h2>
              <p class="caption">
                <span class="mono">{{ sel.key }} · {{ sel.kind }}</span>
                <template v-if="sel.attempt > 1"> · lần {{ sel.attempt }}</template>
                <template v-if="sel.started_at && sel.ended_at"> · {{ humanDuration(sel.ended_at - sel.started_at) }}</template>
              </p>
            </header>

            <div v-if="sel.status === 'running'" class="live card">
              <div class="preview checker">
                <img v-if="selPreview" :src="selPreview" alt="Ảnh xem trước đang sinh" decoding="async" />
                <div v-else class="preview-ph caption">Chưa có ảnh xem trước</div>
              </div>
              <div class="live-meta">
                <div class="live-row">
                  <strong>{{ selProgress?.label || selProgress?.node || 'Đang chạy' }}</strong>
                  <span class="num secondary">
                    <template v-if="selFraction !== null"><TweenNumber :value="selFraction * 100" />% · </template>
                    <ElapsedTimer v-if="sel.started_at" :from="sel.started_at" />
                  </span>
                </div>
                <div class="bar" :class="{ indet: selFraction === null }">
                  <span class="bar-fill" :style="{ transform: `scaleX(${selFraction ?? 0.3})` }" />
                </div>
                <p v-if="selProgress?.node && selProgress?.label" class="caption">Node: {{ selProgress.node }}</p>
              </div>
            </div>

            <div v-else-if="sel.status === 'waiting_resource'" class="note wait">
              <Hourglass :size="16" />
              <span>
                <strong>{{ waitingLabel(sel.meta.waiting_for) }}</strong>
                — {{ waitingHint(sel.meta.waiting_for) }}
              </span>
            </div>

            <div v-else-if="sel.status === 'awaiting_human'" class="note human">
              <Hand :size="16" />
              <span>Run đang dừng chờ bạn {{ sel.kind === 'gate.pick' ? 'chọn ảnh' : 'duyệt' }}.</span>
              <RouterLink :to="{ path: '/gates', query: { step: sel.id } }" class="btn btn-primary"><Inbox /> Mở hộp duyệt</RouterLink>
            </div>

            <div v-else-if="sel.status === 'failed'" class="note fail">
              <CircleAlert :size="16" />
              <div class="fail-body">
                <strong class="selectable">{{ sel.error?.message ?? 'Lỗi không rõ' }}</strong>
                <span class="caption mono">{{ sel.error?.code }}</span>
                <pre v-if="sel.error?.detail" class="mono pre selectable">{{ typeof sel.error.detail === 'string' ? sel.error.detail : JSON.stringify(sel.error.detail, null, 2) }}</pre>
              </div>
              <button class="btn btn-primary" :disabled="retrying === sel.id" @click="retry(sel)"><RotateCcw /> Thử lại</button>
            </div>

            <p v-else class="caption">{{ STEP_LABEL[sel.status] }}</p>

            <div v-if="selFeatured" class="feature card">
              <AssetViewer :key="selFeatured.blob.hash" :view="selFeatured" compact />
            </div>

            <div v-for="[name, list] in selOutputs" :key="`o-${name}`" class="group">
              <h3 class="section-label">Đầu ra · {{ name }} <span class="num">({{ list.length }})</span></h3>
              <div v-auto-animate class="thumbs">
                <button v-for="b in list" :key="b.hash" class="thumb" :title="b.name ?? b.hash" @click="lightbox = b">
                  <BlobThumb :blob="b" :size="256" />
                </button>
              </div>
            </div>

            <div v-for="[name, list] in selInputs" :key="`i-${name}`" class="group">
              <h3 class="section-label">Đầu vào · {{ name }}</h3>
              <div class="thumbs small">
                <button v-for="b in list" :key="b.hash" class="thumb" @click="lightbox = b">
                  <BlobThumb :blob="b" :size="128" />
                </button>
              </div>
            </div>

            <div v-if="selLogs.length" class="group">
              <h3 class="section-label">Nhật ký</h3>
              <ol class="logs mono selectable">
                <li v-for="(l, i) in selLogs" :key="i" :class="`lv-${l.level}`">{{ l.msg }}</li>
              </ol>
            </div>

            <details v-if="selParams" class="group params">
              <summary class="section-label">Tham số</summary>
              <pre class="mono pre selectable">{{ selParams }}</pre>
            </details>
          </div>
        </Transition>
      </section>
    </div>

    <Lightbox :blob="lightbox" :siblings="selBlobs" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.detail {
  flex: 1;
  min-height: 0;
  display: flex;
}
.rail {
  width: min(460px, 46%);
  flex: none;
  border-right: 0.5px solid var(--separator);
}
.rail.graph {
  width: 58%;
  position: relative;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: var(--fs-callout);
  font-weight: 500;
  color: var(--text-2);
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    box-shadow var(--t-fast);
}
.seg button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  color: var(--text);
  font-weight: 600;
}
.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 14px;
}
.rail-item {
  position: relative;
}
.rail-item + .rail-item::before {
  content: '';
  position: absolute;
  left: 26px;
  top: -14px;
  width: 2px;
  height: 14px;
  background: var(--separator-strong);
  border-radius: 1px;
}
.rail-item.done + .rail-item::before {
  background: var(--st-ok);
}
.run-err {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--st-fail-soft);
  color: var(--st-fail);
  font-size: var(--fs-callout);
}
.run-err span {
  color: var(--text);
}
.skel-list {
  display: grid;
  gap: 14px;
}
.skel {
  height: 64px;
  border-radius: var(--r-md);
  background: var(--bg-sunken);
}
.inspector {
  flex: 1;
}
.insp {
  display: grid;
  gap: 18px;
  max-width: 760px;
}
.insp-head {
  display: grid;
  gap: 2px;
}
.mono {
  font-family: var(--mono);
}
.live {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  padding: 12px;
  align-items: center;
}
.preview {
  aspect-ratio: 1;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.preview-ph {
  height: 100%;
  display: grid;
  place-items: center;
}
.live-meta {
  display: grid;
  gap: 10px;
  align-content: center;
}
.live-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.bar {
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--st-running-soft);
}
.bar-fill {
  display: block;
  height: 100%;
  background: var(--st-running);
  transform-origin: left;
  transition: transform 320ms var(--ease-out);
}
.bar.indet .bar-fill {
  animation: indet 1.4s var(--ease-out) infinite;
}
@keyframes indet {
  from {
    transform: translateX(-100%) scaleX(0.3);
  }
  to {
    transform: translateX(340%) scaleX(0.3);
  }
}
.note {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  border-radius: var(--r-md);
  box-shadow: var(--hairline);
}
.note > span {
  flex: 1;
}
.note.wait {
  background: var(--st-waiting-soft);
  color: var(--text);
}
.note.wait svg,
.note.wait strong {
  color: var(--st-waiting);
}
.note.human {
  align-items: center;
  background: var(--st-human-soft);
}
.note.human > svg {
  color: var(--st-human);
}
.note.fail {
  background: var(--st-fail-soft);
}
.note.fail > svg {
  color: var(--st-fail);
  margin-top: 2px;
}
.fail-body {
  flex: 1;
  display: grid;
  gap: 4px;
  min-width: 0;
}
.pre {
  margin: 4px 0 0;
  padding: 10px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow: auto;
}
.group {
  display: grid;
  gap: 8px;
}
.thumbs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.thumbs.small {
  grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
}
.thumb {
  padding: 0;
  border: 0;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--shadow-1), var(--hairline);
  background: none;
  cursor: zoom-in;
  transition: transform var(--t-fast) var(--ease-spring);
  content-visibility: auto;
}
.thumb:hover {
  transform: scale(1.02);
}
.feature {
  height: 360px;
  overflow: hidden;
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
}
.logs {
  list-style: none;
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-sunken);
  font-size: 11.5px;
  max-height: 220px;
  overflow: auto;
  display: grid;
  gap: 2px;
}
.lv-warning,
.lv-warn {
  color: var(--st-waiting);
}
.lv-error {
  color: var(--st-fail);
}
.params summary {
  cursor: pointer;
}
.swap-enter-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.swap-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.swap-enter-from {
  opacity: 0;
  transform: translateX(8px);
}
.swap-leave-to {
  opacity: 0;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-base);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
