<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onClickOutside, onKeyStroke } from '@vueuse/core'
import { ArrowLeft, CircleAlert, CircleCheck, CircleStop, Copy, Ellipsis, FolderKanban, Hourglass, Inbox, List, LoaderCircle, Library, Lock, RotateCcw, TriangleAlert, Waypoints, Workflow } from '@lucide/vue'
import type { BlobRef, StepRow } from '@/api/types'
import { NO_PROJECT, RUN_ACTIVE, STEP_DONE_OK } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import RunStatusBadge from '@/components/RunStatusBadge.vue'
import StepChip from '@/components/StepChip.vue'
import AgentRunBanner from '@/components/AgentRunBanner.vue'
import ElapsedTimer from '@/components/ElapsedTimer.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import AssetViewer from '@/components/viewers/AssetViewer.vue'
import AssetActions from '@/components/AssetActions.vue'
import OpenInEngine from '@/components/OpenInEngine.vue'
import ExportToEngine from '@/components/ExportToEngine.vue'
import NextStepChips from '@/components/NextStepChips.vue'
import ViewsReport from '@/components/ViewsReport.vue'
import MeshReport from '@/components/MeshReport.vue'
import StyleLockReport from '@/components/StyleLockReport.vue'
import { featuredAsset, isImage, isNormalBlob, outputBlobs, sameSize, viewsFor, type AssetView } from '@/components/viewers/detect'
import Lightbox from '@/components/Lightbox.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import EmptyState from '@/components/EmptyState.vue'
import FlowCanvas from '@/components/flow/FlowCanvas.vue'
import GatePicker from '@/components/gate/GatePicker.vue'
import GateApprove from '@/components/gate/GateApprove.vue'
import { buildRunGraph } from '@/composables/graph'
import { errorAdvice } from '@/composables/errorAdvice'
import { useKinds } from '@/stores/catalog'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useFlows } from '@/stores/flows'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useProjects } from '@/stores/projects'
import { appliedText } from '@/composables/projectStyle'
import { gateFromStep, useGates } from '@/stores/gates'
import { exportName, useVersions } from '@/stores/versions'
import ResizeHandle from '@/components/ResizeHandle.vue'
import { usePaneWidth } from '@/composables/paneSize'
import { absoluteTime, ALLOWED_FAILURE_LABEL, allowedFailure, allowedFailureText, humanDuration, kindName, STEP_LABEL, stepName, waitingHint, waitingLabel } from '@/composables/format'
import { flyFromOrigin, takeOrigin, useReducedMotion, type SharedOrigin } from '@/composables/motion'

const props = defineProps<{ id: string }>()
const reducedMotion = useReducedMotion()
const heroOrigin = ref<SharedOrigin | null>(takeOrigin(props.id))
// onHeroStage clears heroOrigin, so the fade-in decision is latched here per run.
const heroFlies = ref(!!heroOrigin.value)
watch(() => props.id, (id) => {
  heroOrigin.value = takeOrigin(id)
  heroFlies.value = !!heroOrigin.value
})
function onHeroStage(el: unknown): void {
  const o = heroOrigin.value
  if (!o || !(el instanceof HTMLElement)) return
  heroOrigin.value = null
  if (!reducedMotion.value && performance.now() - o.at < 2000) requestAnimationFrame(() => flyFromOrigin(o, el))
}
const runs = useRuns()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const kinds = useKinds()
const gates = useGates()
const versions = useVersions()
const projects = useProjects()
const flows = useFlows()
const listW = usePaneWidth('run-steps', 420)
const graphW = usePaneWidth('run-graph', 620)

// The graph needs room next to the inspector; below this width it opens as a list unless chosen per visit.
const GRAPH_MIN_WIDTH = 1440
const MODE_KEY = 'studio.run.view'
function readMode(): 'list' | 'graph' {
  if (window.innerWidth < GRAPH_MIN_WIDTH) return 'list'
  try {
    return localStorage.getItem(MODE_KEY) === 'graph' ? 'graph' : 'list'
  } catch {
    return 'list'
  }
}
const viewMode = ref<'list' | 'graph'>(readMode())
function setMode(m: 'list' | 'graph'): void {
  viewMode.value = m
  try {
    localStorage.setItem(MODE_KEY, m)
  } catch {
    /* storage unavailable */
  }
}

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
  return runs.stepsOf(props.id)
})
const active = computed(() => !!run.value && RUN_ACTIVE.has(run.value.status))
const recipeName = computed(() => (run.value ? (recipes.byId(run.value.recipe_id)?.name ?? run.value.recipe_id) : ''))

const doneCount = computed(() => steps.value.filter((s) => s.status === 'succeeded' || s.status === 'cached').length)
const progressLabel = computed(() => {
  const n = steps.value.length
  if (!n) return ''
  if (!active.value) return `${doneCount.value}/${n} bước`
  const cur = steps.value.findIndex((s) => !STEP_DONE_OK.has(s.status))
  return `Bước ${cur < 0 ? n : cur + 1}/${n}`
})

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

const menuOpen = ref(false)
const confirmCancel = ref(false)
const cancelling = ref(false)
const menuRoot = ref<HTMLElement | null>(null)
function closeMenu(): void {
  menuOpen.value = false
  confirmCancel.value = false
}
onClickOutside(menuRoot, closeMenu)
onKeyStroke('Escape', () => menuOpen.value && closeMenu())
function stepBy(delta: number, e: KeyboardEvent): void {
  const t = e.target as HTMLElement | null
  if (lightbox.value || t?.closest('input, textarea, select, [contenteditable="true"]')) return
  const list = steps.value
  const i = list.findIndex((s) => s.id === selected.value?.id)
  const next = list[Math.min(list.length - 1, Math.max(0, (i < 0 ? 0 : i) + delta))]
  if (!next) return
  e.preventDefault()
  manualId.value = next.id
}
onKeyStroke('ArrowRight', (e) => stepBy(1, e))
onKeyStroke('ArrowLeft', (e) => stepBy(-1, e))

async function cancel(): Promise<void> {
  cancelling.value = true
  try {
    await runs.cancel(props.id)
    toasts.push('Đã huỷ lượt chạy', 'info')
    closeMenu()
  } catch (e) {
    toasts.error(e, 'Không huỷ được lượt chạy')
  } finally {
    cancelling.value = false
  }
}
async function copyId(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.id)
    toasts.push('Đã copy mã lượt chạy', 'success', 2000)
  } catch (e) {
    toasts.error(e, 'Không copy được')
  }
  closeMenu()
}

const sel = computed(() => selected.value)
const selProgress = computed(() => (sel.value ? runs.progress.get(sel.value.id) : undefined))
const selFraction = computed(() => {
  const p = selProgress.value
  return p && p.max ? Math.min(1, p.value / p.max) : null
})
const selPreview = computed(() => (sel.value ? runs.previews.get(sel.value.id) : undefined))
const selLogs = computed(() => (sel.value ? (runs.logs.get(sel.value.id) ?? []).slice(-120) : []))
const selMeta = computed(() => {
  const s = sel.value
  if (!s) return ''
  const tool = kindName(s.kind)
  return [
    tool !== stepName(s) ? tool : '',
    s.attempt > 1 ? `lần ${s.attempt}` : '',
    s.started_at && s.ended_at ? humanDuration(s.ended_at - s.started_at) : ''
  ]
    .filter(Boolean)
    .join(' · ')
})
const selAwaiting = computed(() => sel.value?.status === 'awaiting_human')
// These kinds render their report as a readable block below; the raw JSON tile would only duplicate it.
const REPORT_KINDS = new Set(['views.qwen_checked', 'mesh.checked', 'op.style_audit'])
const selOutputs = computed(() =>
  sel.value
    ? Object.entries(sel.value.outputs).filter(([k, v]) => v.length && !(k === 'report' && REPORT_KINDS.has(sel.value!.kind)))
    : []
)
const selInputs = computed(() => (sel.value && !selAwaiting.value ? Object.entries(sel.value.inputs).filter(([, v]) => v.length) : []))
const selParams = computed(() => (sel.value && Object.keys(sel.value.params).length ? JSON.stringify(sel.value.params, null, 2) : ''))
const selAdvice = computed(() => (sel.value?.status === 'failed' ? errorAdvice(sel.value.error) : null))
const selErrorDetail = computed(() => {
  const d = sel.value?.error?.detail
  if (d === undefined || d === null || d === '') return ''
  return typeof d === 'string' ? d : JSON.stringify(d, null, 2)
})
const selGate = computed(() => (sel.value && selAwaiting.value ? (gates.items.get(sel.value.id) ?? gateFromStep(sel.value)) : null))

const PORT_NAMES: Record<string, string> = {
  images: 'Ảnh',
  image: 'Ảnh',
  meshes: 'Mesh',
  mesh: 'Mesh',
  glb: 'Mesh',
  sheet: 'Sprite sheet',
  sheets: 'Sprite sheet',
  views: 'Các góc',
  files: 'Tệp',
  json: 'Dữ liệu',
  text: 'Văn bản',
  spine: 'Spine',
  audio: 'Âm thanh',
  atlas: 'Atlas',
  pages: 'Trang atlas',
  previews: 'Ảnh so sánh',
  frames: 'Frame',
  report: 'Báo cáo'
}
function portName(name: string): string {
  return PORT_NAMES[name] ?? name
}

const graph = computed(() => buildRunGraph(steps.value, kinds.byKind, selected.value?.id ?? null))
const graphFit = computed(() => `${props.id}:${steps.value.length}:${viewMode.value}`)
function selectByKey(key: string): void {
  const s = steps.value.find((x) => x.key === key)
  if (s) select(s)
}

// Pass-through normal steps (ndirs, ndown, anim_ndown) carry neither a param nor normal-looking names on every
// frame (unmirrored frames keep the render's names), so the key pairing with a same-kind color step decides.
const colorKeyOf = (key: string): string => key.replace(/_normal$/, '').replace(/(^|_)n([^_]+)$/, '$1$2')
const isNormalStep = (s: StepRow): boolean => {
  if (s.params.normal === true || s.meta?.normal === true) return true
  const ck = colorKeyOf(s.key)
  if (ck !== s.key && steps.value.some((x) => x.key === ck && x.kind === s.kind)) return true
  if (Object.keys(s.inputs).some((p) => /^normal(_|$)/.test(p))) return true
  const blobs = outputBlobs(s.outputs)
  return blobs.length > 0 && blobs.every(isNormalBlob)
}
// Normal-map sheets run as their own step keyed after the color one: sheet → nsheet, anim_sheet → anim_nsheet.
function normalPartner(s: StepRow): BlobRef | undefined {
  const keys = [s.key.replace(/(^|_)([^_]+)$/, '$1n$2'), `${s.key}_normal`]
  const p = steps.value.find((x) => keys.includes(x.key) && STEP_DONE_OK.has(x.status) && isNormalStep(x))
  return p ? (p.outputs.sheet ?? outputBlobs(p.outputs)).find(isImage) : undefined
}
function withNormal(view: AssetView | null | undefined, s: StepRow): AssetView | null {
  if (!view || view.kind !== 'sprite' || view.normal || !view.sheet) return view ?? null
  const n = normalPartner(s)
  const normal = n && sameSize(n, view.sheet) ? n : undefined
  return normal ? { ...view, normal } : view
}

const lightbox = ref<BlobRef | null>(null)
const selBlobs = computed(() => (sel.value ? [...Object.values(sel.value.outputs), ...Object.values(sel.value.inputs)].flat() : []))
const selFeatured = computed(() => {
  const s = sel.value
  if (!s || selAwaiting.value || hero.value?.step.id === s.id) return null
  return STEP_DONE_OK.has(s.status) ? withNormal(featuredAsset(outputBlobs(s.outputs)), s) : null
})

const finalStep = computed(() =>
  run.value?.status === 'succeeded'
    ? [...steps.value].reverse().find((s) => STEP_DONE_OK.has(s.status) && Object.values(s.outputs).some((v) => v.length) && !isNormalStep(s))
    : undefined
)
const BUNDLE_KINDS = ['op.sheet', 'op.spine_export']
const bundleStepId = computed(() => {
  if (!finalStep.value) return undefined
  const ok = steps.value.filter((s) => (s.status === 'succeeded' || s.status === 'cached') && !isNormalStep(s))
  return (BUNDLE_KINDS.map((k) => ok.find((s) => s.kind === k)).find(Boolean) ?? finalStep.value).id
})
// A run can finish with more than one deliverable worth showing: front + back rigs (`back_*` ports on the final
// gate) or a still sheet plus a walk sheet.
const heroVariants = computed(() => {
  const s = finalStep.value
  if (!s) return []
  const ports = Object.entries(s.outputs)
  if (ports.some(([k]) => k.startsWith('back_'))) {
    return [
      { label: 'Mặt trước', step: s, blobs: ports.filter(([k]) => !k.startsWith('back_')).flatMap(([, v]) => v) },
      { label: 'Mặt sau', step: s, blobs: ports.filter(([k]) => k.startsWith('back_')).flatMap(([, v]) => v), bundle: 'back_export' }
    ]
  }
  const ok = (key: string) => steps.value.find((x) => x.key === key && (x.status === 'succeeded' || x.status === 'cached'))
  const still = ok('sheet')
  const walk = ok('walk') ?? ok('anim_sheet')
  if (still && walk && walk.kind === 'op.sheet') {
    return [
      { label: 'Đứng yên', step: still, blobs: outputBlobs(still.outputs) },
      { label: walk.key === 'walk' ? 'Đi bộ' : 'Hoạt ảnh', step: walk, blobs: outputBlobs(walk.outputs), bundle: walk.key }
    ]
  }
  return [{ label: '', step: s, blobs: outputBlobs(s.outputs) }]
})
const heroPick = ref(0)
watch(() => props.id, () => (heroPick.value = 0))
const heroBundleId = computed(() => {
  const v = heroVariants.value[heroPick.value] as { bundle?: string } | undefined
  return (v?.bundle && steps.value.find((x) => x.key === v.bundle)?.id) || bundleStepId.value
})
const hero = computed(() => {
  const v = heroVariants.value[Math.min(heroPick.value, heroVariants.value.length - 1)]
  if (!v) return null
  const lead = v.blobs.find(isImage) ?? v.blobs[0]
  const view = withNormal(featuredAsset(v.blobs) ?? (lead ? viewsFor(lead, v.blobs)[0] : undefined), v.step)
  return view ? { view, step: v.step } : null
})

const runVersions = computed(() => versions.list.filter((v) => v.run_id === props.id))
const heroVersion = computed(() => {
  const h = hero.value
  return h ? (runVersions.value.find((v) => v.blob.hash === h.view.blob.hash) ?? runVersions.value[0]) : undefined
})
const draftIds = computed(() => runVersions.value.filter((v) => v.status === 'draft').map((v) => v.id))
const inLibrary = computed(() => runVersions.value.length > 0 && runVersions.value.every((v) => v.status === 'approved'))
watch(
  () => [!!hero.value, conn.transport] as const,
  ([has, t]) => {
    if (has && t && !versions.loaded) void versions.load().catch(() => undefined)
  },
  { immediate: true }
)
const approving = ref(false)
async function addToLibrary(): Promise<void> {
  if (!draftIds.value.length) return
  approving.value = true
  try {
    await versions.setStatusMany(draftIds.value, 'approve')
    toasts.push('Đã đưa vào thư viện', 'success', 2500)
  } catch (e) {
    toasts.error(e, 'Không đưa vào thư viện được')
  } finally {
    approving.value = false
  }
}

const runProject = computed(() => {
  const r = run.value
  if (!r || !r.project_id || r.project_id === NO_PROJECT) return null
  const meta = (r.params?.$meta ?? {}) as { project_applied?: unknown }
  const applied = appliedText(meta.project_applied, recipes.byId(r.recipe_id)?.inputs)
  return { id: r.project_id, name: projects.byId(r.project_id)?.name ?? 'Dự án', applied }
})
watch(
  () => runProject.value?.id,
  (id) => id && !projects.byId(id) && conn.transport && void projects.refresh(id).catch(() => undefined),
  { immediate: true }
)

function segTitle(s: StepRow): string {
  return `${stepName(s)}: ${allowedFailure(s) === null ? STEP_LABEL[s.status] : ALLOWED_FAILURE_LABEL}`
}

const canLockStyle = computed(() => run.value?.recipe_id === 'style-lock' && run.value.status === 'succeeded' && !!runProject.value)
const lockingStyle = ref(false)
async function lockStyle(): Promise<void> {
  const p = runProject.value
  if (!p || lockingStyle.value) return
  lockingStyle.value = true
  try {
    const bible = (await api().lockStyleFromRun(p.id, props.id)).bible
    toasts.push(`Đã đặt style cho dự án ${p.name}${bible ? ` (bản ${bible.version})` : ''}`, 'success', 3000)
  } catch (e) {
    toasts.error(e, 'Không đặt được style project')
  } finally {
    lockingStyle.value = false
  }
}

const workflowId = computed(() => {
  const id = (run.value?.params?.$meta as { workflow_run_id?: unknown } | undefined)?.workflow_run_id
  return typeof id === 'string' && id ? id : null
})
watch(
  [workflowId, () => conn.transport],
  ([id, t]) => id && t && !flows.runs.has(id) && void flows.loadRuns(),
  { immediate: true }
)

const runWarnings = computed(() => {
  const w = (run.value?.params?.$meta as { warnings?: unknown } | undefined)?.warnings
  return Array.isArray(w) ? w.filter((x): x is string => typeof x === 'string' && x.trim() !== '').map(allowedFailureText) : []
})
const selSkipWarning = computed(() => (sel.value ? allowedFailure(sel.value) : null))
</script>

<template>
  <div class="view">
    <PageHeader :title="run?.title || recipeName || 'Lượt chạy'">
      <template #leading>
        <RouterLink to="/runs" class="btn btn-plain btn-icon" aria-label="Về danh sách lượt chạy"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="run" #subtitle>
        {{ recipeName }} · {{ absoluteTime(run.created_at) }} ·
        <ElapsedTimer v-if="active" :from="run.created_at" />
        <span v-else-if="run.ended_at" class="num">{{ humanDuration(run.ended_at - run.created_at) }}</span>
      </template>
      <RouterLink
        v-if="runProject"
        :to="`/projects/${runProject.id}`"
        class="proj-badge" :aria-label="`Dự án ${runProject.name}`"
        :title="runProject.applied ? `Dự án ${runProject.name} · đã áp: ${runProject.applied}` : `Dự án ${runProject.name}`"
      >
        <FolderKanban :size="13" aria-hidden="true" />
        <span class="pb-name lbl">{{ runProject.name }}</span>
        <span v-if="runProject.applied" class="pb-applied num lbl">· áp {{ runProject.applied.split(', ').length }}</span>
      </RouterLink>
      <ExportToEngine v-if="runProject && run?.status === 'succeeded'" :project-id="runProject.id" />
      <RouterLink v-if="workflowId" :to="`/flows/runs/${workflowId}`" class="proj-badge" aria-label="Mở sơ đồ workflow" title="Mở sơ đồ workflow của lượt chạy này">
        <Waypoints :size="13" aria-hidden="true" />
        <span class="pb-name lbl">Thuộc workflow {{ flows.runs.get(workflowId)?.title ?? '' }}</span>
      </RouterLink>
      <div class="seg" role="radiogroup" aria-label="Kiểu xem">
        <button type="button" role="radio" :aria-checked="viewMode === 'list'" :class="{ on: viewMode === 'list' }" title="Danh sách bước" aria-label="Danh sách" @click="setMode('list')"><List :size="14" /> <span class="lbl">Danh sách</span></button>
        <button type="button" role="radio" :aria-checked="viewMode === 'graph'" :class="{ on: viewMode === 'graph' }" title="Sơ đồ bước" aria-label="Sơ đồ" @click="setMode('graph')"><Workflow :size="14" /> <span class="lbl">Sơ đồ</span></button>
      </div>
      <RunStatusBadge v-if="run" :status="run.status" />
      <div ref="menuRoot" class="more">
        <button
          type="button"
          class="btn btn-plain btn-icon"
          aria-haspopup="menu"
          :aria-expanded="menuOpen"
          aria-label="Thao tác khác"
          title="Thao tác khác"
          @click="menuOpen ? closeMenu() : (menuOpen = true)"
        >
          <Ellipsis />
        </button>
        <Transition name="menu-pop">
          <div v-if="menuOpen" class="menu" role="menu">
            <div v-if="!confirmCancel" class="menu-list">
              <button type="button" role="menuitem" class="mi" @click="copyId"><Copy :size="15" /> Copy mã lượt chạy</button>
              <template v-if="active">
                <div class="menu-sep" role="separator" />
                <button type="button" role="menuitem" class="mi danger" @click="confirmCancel = true"><CircleStop :size="15" /> Huỷ lượt chạy…</button>
              </template>
            </div>
            <div v-else class="confirm" role="alertdialog" aria-labelledby="cancel-q">
              <strong id="cancel-q">Huỷ lượt chạy này?</strong>
              <p class="caption">Bước đang chạy sẽ dừng ngay. Không hoàn tác được.</p>
              <div class="confirm-row">
                <button type="button" class="btn" @click="confirmCancel = false">Không</button>
                <button type="button" class="btn btn-danger" :disabled="cancelling" @click="cancel">
                  <LoaderCircle v-if="cancelling" class="spin" />
                  Huỷ lượt chạy
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </PageHeader>

    <div
      v-if="steps.length"
      class="runbar"
      role="progressbar"
      :aria-valuemin="0"
      :aria-valuemax="steps.length"
      :aria-valuenow="doneCount"
      :aria-valuetext="progressLabel"
    >
      <div class="segs">
        <span v-for="s in steps" :key="s.id" class="sg" :class="`sg-${s.status}`" :title="segTitle(s)" />
      </div>
      <span class="runbar-label caption num">{{ progressLabel }}</span>
    </div>

    <AgentRunBanner :run-id="id" />

    <NextStepChips v-if="run?.status === 'succeeded' && finalStep" :recipe-id="run.recipe_id" :outputs="finalStep.outputs" :project-id="run.project_id" />

    <div v-if="runWarnings.length" class="note warn run-warn" role="status">
      <TriangleAlert :size="16" />
      <ul>
        <li v-for="(w, i) in runWarnings" :key="i" class="selectable">{{ w }}</li>
      </ul>
    </div>

    <EmptyState v-if="loadError && !run" :icon="CircleAlert" title="Không mở được lượt chạy" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>

    <div v-else class="detail">
      <section v-if="viewMode === 'graph'" class="rail graph" aria-label="Sơ đồ các bước" :style="{ '--pane-w': `${graphW}px` }">
        <FlowCanvas
          v-if="steps.length"
          :id="`run-${id}`"
          :nodes="graph.nodes"
          :edges="graph.edges"
          :fit-key="graphFit"
          :focus-id="selected?.key ?? null"
          :min-fit-zoom="0.75"
          :fit-width-zoom="0.4"
          scroll-pan
          :minimap="steps.length > 6"
          @node-click="selectByKey"
        />
      </section>
      <section v-else class="rail scroll" aria-label="Các bước" :style="{ '--pane-w': `${listW}px` }">
        <ol v-if="steps.length" class="steps">
          <li v-for="s in steps" :key="s.id" class="rail-item" :class="`c-${s.status}`">
            <StepChip :step="s" :progress="runs.progress.get(s.id)" :selected="selected?.id === s.id" @select="select(s)" />
          </li>
        </ol>
        <div v-else-if="loading" class="skel-list">
          <div v-for="i in 4" :key="i" class="skel" />
        </div>
      </section>

      <ResizeHandle v-if="sel && viewMode === 'graph'" v-model="graphW" label="Sơ đồ các bước" :default-size="620" :min="360" :max="1200" />
      <ResizeHandle v-else-if="sel" v-model="listW" label="Danh sách bước" :default-size="420" :min="300" :max="680" />
      <section v-if="sel" class="inspector scroll">
        <section v-if="hero" :key="hero.step.run_id" class="hero" :class="{ 'hero-in': !heroFlies }" aria-label="Kết quả">
          <div class="hero-head">
            <h2 class="section-label">Kết quả</h2>
            <span class="caption">{{ stepName(hero.step) }}</span>
            <div v-if="heroVariants.length > 1" class="seg hero-seg" role="radiogroup" aria-label="Phiên bản kết quả">
              <button
                v-for="(v, i) in heroVariants"
                :key="v.label"
                type="button"
                role="radio"
                :aria-checked="heroPick === i"
                :class="{ on: heroPick === i }"
                @click="heroPick = i"
              >
                {{ v.label }}
              </button>
            </div>
          </div>
          <div :ref="onHeroStage" class="hero-stage card">
            <AssetViewer :key="hero.view.blob.hash" :view="hero.view" />
          </div>
          <div class="hero-actions">
            <AssetActions
              :hash="hero.view.blob.hash"
              :blob="hero.view.blob"
              :filename="heroVersion ? exportName(heroVersion) : (hero.view.blob.name ?? undefined)"
              :run-id="id"
              :version-id="heroVersion?.id"
              :step-id="heroBundleId"
            />
            <OpenInEngine v-if="heroBundleId" :step-id="heroBundleId" :outputs="steps.find((s) => s.id === heroBundleId)?.outputs" />
            <span class="spacer" />
            <Transition name="fade" mode="out-in">
              <span v-if="inLibrary" key="in" class="in-lib"><CircleCheck :size="16" /> Đã có trong thư viện</span>
              <button v-else-if="draftIds.length" key="add" class="btn btn-primary" :disabled="approving" @click="addToLibrary">
                <LoaderCircle v-if="approving" class="spin" />
                <Library v-else />
                Đưa vào thư viện
              </button>
            </Transition>
          </div>
        </section>

        <Transition name="swap" mode="out-in">
          <div :key="sel.id" class="insp" :class="{ 'after-hero': hero }" aria-live="polite">
            <header class="insp-head">
              <p v-if="hero" class="section-label">Bước đang chọn</p>
              <h2 class="title">{{ stepName(sel) }}</h2>
              <p v-if="selMeta" class="caption num">{{ selMeta }}</p>
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

            <div v-else-if="selGate" class="gate-inline">
              <div class="gate-inline-head">
                <span class="human-tag">{{ selGate.kind === 'pick' ? 'Chờ bạn chọn ảnh' : 'Chờ bạn duyệt' }}</span>
                <RouterLink :to="{ path: '/gates', query: { step: sel.id } }" class="btn btn-plain"><Inbox /> Mở hộp duyệt</RouterLink>
              </div>
              <GatePicker v-if="selGate.kind === 'pick'" :gate="selGate" inline :keyboard="false" />
              <GateApprove v-else :gate="selGate" inline :keyboard="false" />
            </div>

            <div v-else-if="selAdvice" class="callout" role="alert">
              <CircleAlert :size="18" class="callout-ic" />
              <div class="callout-body">
                <strong class="callout-title">{{ selAdvice.title }}</strong>
                <p class="callout-hint">{{ selAdvice.hint }}</p>
                <div class="callout-actions">
                  <button class="btn btn-primary" :disabled="retrying === sel.id" @click="retry(sel)">
                    <LoaderCircle v-if="retrying === sel.id" class="spin" />
                    <RotateCcw v-else />
                    {{ selAdvice.retryLabel }}
                  </button>
                  <RouterLink v-for="l in selAdvice.links" :key="l.to" :to="l.to" class="btn">{{ l.label }}</RouterLink>
                </div>
              </div>
            </div>

            <div v-else-if="selSkipWarning !== null" class="note warn" role="status">
              <TriangleAlert :size="16" />
              <span>
                <strong>{{ ALLOWED_FAILURE_LABEL }}</strong>
                <template v-if="selSkipWarning"> — {{ selSkipWarning }}</template>
                <template v-else-if="sel.error"> — {{ sel.error.message }}</template>
              </span>
            </div>

            <p v-else class="caption">{{ STEP_LABEL[sel.status] }}</p>

            <div v-if="selFeatured" class="feature card">
              <AssetViewer :key="selFeatured.blob.hash" :view="selFeatured" compact />
            </div>

            <div v-for="[name, list] in selOutputs" :key="`o-${name}`" class="group">
              <h3 class="section-label">{{ portName(name) }} đầu ra <span class="num">({{ list.length }})</span></h3>
              <div v-auto-animate class="thumbs">
                <button v-for="b in list" :key="b.hash" class="thumb" :title="b.name ?? b.hash" @click="lightbox = b">
                  <BlobThumb :blob="b" :size="256" />
                </button>
              </div>
            </div>

            <ViewsReport v-if="sel.kind === 'views.qwen_checked' && sel.outputs.report?.[0]" :report="sel.outputs.report[0]" />
            <MeshReport v-if="(sel.kind === 'mesh.checked' || sel.outputs.report?.[0]?.name === 'mesh_report.json') && sel.outputs.report?.[0]" :report="sel.outputs.report[0]" />

            <StyleLockReport v-if="sel.kind === 'op.style_audit' && sel.outputs.report?.[0]" :report="sel.outputs.report[0]" />
            <button
              v-if="sel.kind === 'op.style_audit' && canLockStyle"
              type="button"
              class="btn btn-primary lock-btn"
              :disabled="lockingStyle"
              :title="`Dùng ảnh mỏ neo của lượt này làm style chung của dự án ${runProject?.name}; lượt chạy mới trong dự án sẽ tự áp`"
              @click="lockStyle"
            >
              <LoaderCircle v-if="lockingStyle" class="spin" /> <Lock v-else /> Đặt làm style project
            </button>

            <div v-for="[name, list] in selInputs" :key="`i-${name}`" class="group">
              <h3 class="section-label">{{ portName(name) }} đầu vào</h3>
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

            <details class="group tech">
              <summary class="section-label">Chi tiết kỹ thuật</summary>
              <dl class="tech-list">
                <dt>Mã bước</dt>
                <dd class="mono selectable">{{ sel.key }}</dd>
                <dt>Loại</dt>
                <dd class="mono selectable">{{ sel.kind }}</dd>
                <template v-if="sel.error">
                  <dt>Mã lỗi</dt>
                  <dd class="mono selectable">{{ sel.error.code }}</dd>
                  <dt>Thông báo</dt>
                  <dd class="selectable">{{ sel.error.message }}</dd>
                </template>
              </dl>
              <pre v-if="selErrorDetail" class="mono pre selectable">{{ selErrorDetail }}</pre>
              <template v-if="selParams">
                <h4 class="caption">Tham số</h4>
                <pre class="mono pre selectable">{{ selParams }}</pre>
              </template>
            </details>
          </div>
        </Transition>
      </section>
    </div>

    <Lightbox :blob="lightbox" :siblings="selBlobs" :run-id="id" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.view :deep(.page-header .t h1) {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  white-space: normal;
  font-size: 20px;
  line-height: 1.25;
}
.view :deep(.page-header .sub) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.detail {
  flex: 1;
  min-height: 0;
  display: flex;
}
.rail {
  width: var(--pane-w, 420px);
  max-width: 60%;
  flex: none;
  border-right: 1px solid var(--hair-soft);
}
.rail.graph {
  max-width: calc(100% - 320px);
  position: relative;
}
.more {
  position: relative;
  margin-left: var(--sp-2);
}
.menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 20;
  min-width: 220px;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  transform-origin: top right;
}
.menu-list {
  display: grid;
}
.mi {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.mi:hover,
.mi:focus-visible {
  background: var(--bg-hover);
}
.mi.danger {
  color: var(--danger);
}
.menu-sep {
  height: 0.5px;
  margin: 4px 6px;
  background: var(--separator);
}
.confirm {
  display: grid;
  gap: 6px;
  padding: 8px 8px 6px;
  width: 260px;
}
.confirm p {
  margin: 0;
}
.confirm-row {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}

.runbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: var(--sp-2) var(--sp-6);
  border-bottom: 1px solid var(--hair-soft);
}
.segs {
  flex: 1;
  display: flex;
  gap: 3px;
  min-width: 0;
}
.sg {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--hair);
  transition: background-color var(--t-base) var(--ease-out);
}
.sg-succeeded,
.sg-cached {
  background: var(--st-ok);
}
.sg-running {
  background: var(--st-running);
  animation: soft-pulse 1.6s var(--ease-out) infinite;
}
.sg-ready,
.sg-waiting_resource {
  background: var(--st-waiting);
}
.sg-awaiting_human {
  background: var(--st-human);
}
.sg-failed {
  background: var(--st-fail);
}
.sg-skipped,
.sg-cancelled {
  background: repeating-linear-gradient(90deg, var(--separator-strong) 0 4px, transparent 4px 7px);
}
.runbar-label {
  flex: none;
  min-width: 7ch;
  text-align: right;
}

.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-3);
}
.rail-item {
  position: relative;
}
.rail-item + .rail-item::before {
  content: '';
  position: absolute;
  left: 28px;
  top: calc(-1 * var(--sp-3));
  width: 2px;
  height: var(--sp-3);
  background: var(--hair);
  border-radius: 1px;
  transition: background-color var(--t-base) var(--ease-out);
}
.rail-item.c-succeeded::before,
.rail-item.c-cached::before {
  background: var(--st-ok);
}
.rail-item.c-running::before {
  background: var(--st-running);
}
.rail-item.c-awaiting_human::before {
  background: var(--st-human);
}
.rail-item.c-failed::before {
  background: var(--st-fail);
}
.rail-item.c-skipped::before,
.rail-item.c-cancelled::before {
  background: repeating-linear-gradient(180deg, var(--separator-strong) 0 3px, transparent 3px 6px);
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
  min-width: 0;
  container-type: inline-size;
}
@container (max-width: 560px) {
  .insp .live {
    grid-template-columns: minmax(0, 1fr);
  }
  .insp .preview {
    max-width: 240px;
  }
}
.hero-in {
  animation: hero-in 320ms var(--ease-spring) both;
}
@keyframes hero-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
}
.menu-pop-enter-active {
  transition:
    opacity 140ms var(--ease-out),
    transform 200ms var(--ease-spring);
}
.menu-pop-leave-active {
  transition:
    opacity 100ms var(--ease-out),
    transform 100ms var(--ease-out);
}
.menu-pop-enter-from {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
.menu-pop-leave-to {
  opacity: 0;
  transform: scale(0.98);
}
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  max-width: 900px;
  margin-bottom: 24px;
}
.hero-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.hero-seg {
  margin-left: auto;
  align-self: center;
}
.hero-stage {
  height: clamp(320px, 52vh, 560px);
  overflow: hidden;
  background: var(--bg-stage);
}
.hero-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.spacer {
  flex: 1;
}
.in-lib {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--st-ok);
}

.insp {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  max-width: 760px;
}
.insp.after-hero {
  padding-top: var(--sp-5);
  border-top: 1px solid var(--hair-soft);
}
.insp-head {
  display: grid;
  gap: 2px;
}
.insp-head .section-label {
  margin: 0 0 4px;
}
.mono {
  font-family: var(--mono);
}
.live {
  display: grid;
  grid-template-columns: minmax(120px, 240px) minmax(0, 1fr);
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

.note.warn {
  background: var(--st-waiting-soft);
  color: var(--text);
}
.note.warn svg,
.note.warn strong {
  color: var(--st-waiting);
}
.run-warn {
  flex: none;
  margin: 6px var(--sp-6) 0;
}
.run-warn ul {
  flex: 1;
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 2px;
}

.gate-inline {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}
.gate-inline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.human-tag {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--st-human);
  background: var(--st-human-soft);
}

.callout {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  border-radius: var(--r-md);
  background: var(--danger-soft, var(--st-fail-soft));
  box-shadow: var(--hairline);
}
.callout-ic {
  flex: none;
  margin-top: 1px;
  color: var(--danger, var(--st-fail));
}
.callout-body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}
.callout-title {
  font-weight: 600;
}
.callout-hint {
  margin: 0;
  color: var(--text-2);
}
.callout-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.pre {
  margin: 4px 0 0;
  padding: 10px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  font-size: 12px;
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
  font-size: 12px;
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
.tech summary {
  cursor: pointer;
}
.tech-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 14px;
  margin: 4px 0 0;
  font-size: var(--fs-callout);
}
.tech-list dt {
  color: var(--text-3);
}
.tech-list dd {
  margin: 0;
  min-width: 0;
  word-break: break-word;
}
.tech h4 {
  margin: 8px 0 0;
}
.spin {
  animation: spin 1s linear infinite;
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
.lock-btn {
  justify-self: start;
  margin: 4px 0 8px;
}
.proj-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 200px;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  text-decoration: none;
  transition: background-color var(--t-fast);
}
.proj-badge:hover {
  background: color-mix(in srgb, var(--accent) 20%, transparent);
}
.pb-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pb-applied {
  flex: none;
  font-weight: 500;
  opacity: 0.75;
}
</style>
