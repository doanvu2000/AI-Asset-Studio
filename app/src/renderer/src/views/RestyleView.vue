<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Check,
  CircleAlert,
  ExternalLink,
  FolderOpen,
  LoaderCircle,
  Paintbrush,
  Play,
  ScanSearch,
  ShieldCheck,
  Undo2,
  Upload,
  X
} from '@lucide/vue'
import type { BlobRef, StepRow } from '@/api/types'
import { NO_PROJECT, RUN_ACTIVE, STEP_DONE_OK } from '@/api/types'
import type { RestyleApplyResult, RestyleBackup, RestyleKind, RestyleReport, RestyleScan } from '@/api/restyleTypes'
import PageHeader from '@/components/PageHeader.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import BlobImage from '@/components/BlobImage.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RunStatusBadge from '@/components/RunStatusBadge.vue'
import { api, useConnection } from '@/stores/connection'
import { useRuns } from '@/stores/runs'
import { useProjects } from '@/stores/projects'
import { useToasts } from '@/stores/toasts'
import { humanDuration } from '@/composables/format'
import { blobJson } from '@/components/viewers/fetchBlob'

const RECIPE = 'restyle-folder'
const TRIAL = 6
const FOLDER_KEY = 'restyle.folder'
const KIND_LABEL: Record<RestyleKind, string> = { sprite: 'Sprite', sheet: 'Sheet', ui: 'UI 9-slice', tile: 'Tile', texture: 'Texture' }
const STEPS: { key: string; label: string }[] = [
  { key: 'scan', label: 'Quét' },
  { key: 'describe', label: 'Claude mô tả' },
  { key: 'plan', label: 'Chuẩn bị' },
  { key: 'edit', label: 'Klein vẽ lại' },
  { key: 'matte', label: 'Tách nền' },
  { key: 'fit', label: 'Ép khớp + QA' },
  { key: 'approve', label: 'Duyệt' },
  { key: 'apply', label: 'Báo cáo ghi' }
]
const MATTES = [
  { id: 'auto', label: 'Tự động', hint: 'Giữ alpha gốc; dùng BiRefNet khi Klein đổi silhouette' },
  { id: 'original', label: 'Alpha gốc', hint: 'Luôn giữ đúng alpha gốc (an toàn nhất cho collider / hitbox)' },
  { id: 'birefnet', label: 'BiRefNet', hint: 'Lấy alpha mới từ ảnh đã vẽ lại' }
]

const route = useRoute()
const router = useRouter()
const conn = useConnection()
const runs = useRuns()
const projects = useProjects()
const toasts = useToasts()

function readFolder(): string {
  try {
    return localStorage.getItem(FOLDER_KEY) ?? ''
  } catch {
    return ''
  }
}

const folder = ref(typeof route.query.folder === 'string' ? route.query.folder : readFolder())
const include = ref('')
const exclude = ref('')
const showFilter = ref(false)
const scanning = ref(false)
const scan = ref<RestyleScan | null>(null)
const scanError = ref<string | null>(null)
const style = ref('')
const styleRef = ref<BlobRef[]>([])
const matte = ref('auto')
const starting = ref<'trial' | 'full' | null>(null)
const projectId = ref<string>(projects.activeId ?? NO_PROJECT)
const project = computed(() => projects.byId(projectId.value))
watch(projectId, (id) => projects.setActive(id))

const runId = computed(() => (typeof route.query.run === 'string' ? route.query.run : null))
const HOLDER = `restyle:${Math.random().toString(36).slice(2)}`
let held: string | null = null
watch(
  () => [conn.transport, runId.value] as const,
  ([t, id]) => {
    if (held && held !== id) runs.closeDetail(held, HOLDER)
    held = id
    if (t && id) void runs.loadDetail(id, HOLDER).catch((e) => toasts.error(e, 'Không tải được lượt restyle'))
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  if (held) runs.closeDetail(held, HOLDER)
})

const run = computed(() => (runId.value ? runs.runs.get(runId.value) : undefined))
const steps = computed(() => (runId.value ? runs.stepsOf(runId.value) : []))
const stepByKey = computed(() => new Map(steps.value.map((s) => [s.key, s])))
const isTrial = computed(() => Number(run.value?.inputs.sample ?? 0) > 0)
const active = computed(() => !!run.value && RUN_ACTIVE.has(run.value.status))

const canScan = computed(() => /^([a-zA-Z]:[\\/]|\/)/.test(folder.value.trim()) && !scanning.value)

async function pickFolder(): Promise<void> {
  if (!window.studio) return void toasts.push('Chọn thư mục chỉ có trong ứng dụng desktop; dán đường dẫn vào ô', 'info')
  const p = await window.studio.pickPath({ kind: 'dir', title: 'Chọn thư mục project (Assets/… hoặc thư mục Godot)', defaultPath: folder.value || undefined })
  if (p) {
    folder.value = p
    void doScan()
  }
}

async function doScan(): Promise<void> {
  if (!canScan.value) return
  scanning.value = true
  scanError.value = null
  try {
    scan.value = await api().restyleScan({ folder: folder.value.trim(), include: include.value, exclude: exclude.value })
    try {
      localStorage.setItem(FOLDER_KEY, folder.value.trim())
    } catch {
      /* per-viewer convenience only */
    }
  } catch (e) {
    scan.value = null
    scanError.value = e instanceof Error ? e.message : String(e)
  } finally {
    scanning.value = false
  }
}

const kinds = computed(() => (scan.value ? (Object.keys(KIND_LABEL) as RestyleKind[]).filter((k) => scan.value!.counts[k] > 0) : []))
const total = computed(() => scan.value?.items.length ?? 0)

async function start(mode: 'trial' | 'full'): Promise<void> {
  if (!scan.value || starting.value) return
  starting.value = mode
  const inputs: Record<string, unknown> = {
    folder: scan.value.root,
    sample: mode === 'trial' ? TRIAL : 0,
    include: include.value,
    exclude: exclude.value,
    matte: matte.value,
    output: 'dry_run',
    review: true
  }
  if (style.value.trim()) inputs.style = style.value.trim()
  if (styleRef.value[0]) inputs.style_ref = styleRef.value[0].hash
  try {
    const name = scan.value.root.split(/[\\/]/).filter(Boolean).slice(-2).join('/')
    const res = await api().startRun({
      recipe_id: RECIPE,
      inputs,
      title: `Restyle ${name}${mode === 'trial' ? ` (thử ${TRIAL})` : ''}`,
      project_id: projectId.value !== NO_PROJECT ? projectId.value : undefined
    })
    applied.value = null
    report.value = null
    void router.replace({ query: { ...route.query, run: res.run_id, folder: scan.value.root } })
  } catch (e) {
    toasts.error(e, 'Không bắt đầu được lượt restyle')
  } finally {
    starting.value = null
  }
}

function stepCount(s: StepRow | undefined): { done: number; max: number } | null {
  if (!s) return null
  const p = runs.progress.get(s.id)
  if (p && p.max > 1 && (s.key === 'edit' || s.key === 'matte' || s.key === 'fit' || s.key === 'plan')) {
    return { done: Math.min(p.max, Math.floor(p.value)), max: Math.round(p.max) }
  }
  return null
}
function stepFrac(s: StepRow | undefined): number {
  if (!s) return 0
  if (STEP_DONE_OK.has(s.status)) return 1
  const p = runs.progress.get(s.id)
  if (p && p.max > 0) return Math.min(1, p.value / p.max)
  return s.progress ?? 0
}
const WEIGHT: Record<string, number> = { scan: 1, describe: 2, plan: 1, edit: 12, matte: 2, fit: 1, approve: 0, apply: 0.3 }
const overall = computed(() => {
  const sum = STEPS.reduce((a, s) => a + WEIGHT[s.key]!, 0)
  return STEPS.reduce((a, s) => a + WEIGHT[s.key]! * stepFrac(stepByKey.value.get(s.key)), 0) / sum
})
const editUnits = computed(() => {
  const s = stepByKey.value.get('edit')
  const c = stepCount(s)
  if (c) return c
  const lay = stepByKey.value.get('plan')
  const n = Number((lay?.meta as Record<string, unknown> | undefined)?.units ?? 0)
  return n ? { done: s && STEP_DONE_OK.has(s.status) ? n : 0, max: n } : null
})
const eta = computed(() => {
  const u = editUnits.value
  if (!active.value || !u || !scan.value) return null
  return Math.max(0, (u.max - u.done) * scan.value.estimate.edit_s)
})

const report = ref<RestyleReport | null>(null)
const reportHash = computed(() => stepByKey.value.get('fit')?.outputs.restyle?.[0]?.hash ?? null)
watch(
  reportHash,
  async (h) => {
    if (!h) return void (report.value = null)
    try {
      report.value = await blobJson<RestyleReport>(h)
    } catch (e) {
      toasts.error(e, 'Không đọc được báo cáo restyle')
    }
  },
  { immediate: true }
)
const qaScore = computed(() => report.value?.qa.summary.score ?? null)
const gateStep = computed(() => {
  const s = stepByKey.value.get('approve')
  return s && s.status === 'awaiting_human' ? s : undefined
})
const resolving = ref(false)
async function resolve(approved: boolean): Promise<void> {
  if (!gateStep.value || resolving.value) return
  resolving.value = true
  try {
    await api().resolveGate(gateStep.value.id, { approved })
  } catch (e) {
    toasts.error(e, 'Không gửi được kết quả duyệt')
  } finally {
    resolving.value = false
  }
}

const applied = ref<RestyleApplyResult | null>(null)
const backups = ref<RestyleBackup[]>([])
const confirming = ref(false)
const applying = ref(false)
const undoing = ref<string | null>(null)
const canApply = computed(() => run.value?.status === 'succeeded' && !!report.value && !isTrial.value)
const liveBackup = computed(() => backups.value.find((b) => !b.undone_at))

async function loadBackups(): Promise<void> {
  if (!runId.value || !conn.transport) return void (backups.value = [])
  try {
    backups.value = (await api().restyleBackups(runId.value)).backups
  } catch {
    backups.value = []
  }
}
watch(() => [runId.value, run.value?.status, conn.transport], loadBackups, { immediate: true })

async function applyNow(): Promise<void> {
  if (!runId.value || applying.value) return
  applying.value = true
  try {
    applied.value = await api().restyleApply(runId.value)
    confirming.value = false
    const a = applied.value
    toasts.push(`Đã ghi ${a.written} ảnh vào project${a.skipped ? `, bỏ qua ${a.skipped}` : ''} · backup ${a.backup_id ?? ''}`, a.written ? 'success' : 'info')
    await loadBackups()
  } catch (e) {
    toasts.error(e, 'Không áp được vào project')
  } finally {
    applying.value = false
  }
}

async function undo(id: string): Promise<void> {
  if (undoing.value) return
  undoing.value = id
  try {
    const r = await api().restyleUndo(id)
    toasts.push(`Đã khôi phục ${r.restored.length} ảnh${r.conflicts.length ? `; ${r.conflicts.length} ảnh đã bị sửa sau đó nên giữ nguyên` : ''}`, r.conflicts.length ? 'info' : 'success')
    applied.value = null
    await loadBackups()
  } catch (e) {
    toasts.error(e, 'Không hoàn tác được')
  } finally {
    undoing.value = null
  }
}

function fmtS(s: number): string {
  return humanDuration(Math.round(s))
}
const skippedOpen = ref(false)
const reportRows = computed(() => report.value?.items ?? [])
const flagged = computed(() => new Set([...(report.value?.flags.silhouette ?? []), ...(report.value?.flags.low_qa ?? [])]))
function openRun(): void {
  if (runId.value) void router.push(`/runs/${runId.value}`)
}
</script>

<template>
  <div class="view">
    <PageHeader title="Restyle project" subtitle="Vẽ lại cả thư mục theo style dự án, giữ nguyên GUID, .meta/.import và mọi tham chiếu">
      <ProjectSelect v-model="projectId" label="Dự án (style)" />
    </PageHeader>
    <div class="scroll">
      <div class="stack wrap">
        <section class="card pane">
          <div class="pane-head">
            <FolderOpen class="pi" aria-hidden="true" />
            <h2 class="headline">Thư mục project</h2>
            <span v-if="scan" class="tag" :class="`e-${scan.engine}`">{{ scan.engine === 'unity' ? 'Unity' : scan.engine === 'godot' ? 'Godot' : 'Không rõ engine' }}</span>
          </div>
          <div class="folder-row">
            <input
              v-model="folder"
              class="field mono"
              spellcheck="false"
              placeholder="L:\Game\Assets\Sprites  hoặc  D:\GodotGame\art"
              aria-label="Đường dẫn thư mục"
              @keydown.enter="doScan"
            />
            <button type="button" class="btn" @click="pickFolder"><FolderOpen /> Chọn…</button>
            <button type="button" class="btn btn-primary" :disabled="!canScan" @click="doScan">
              <LoaderCircle v-if="scanning" class="spin" /><ScanSearch v-else /> Quét
            </button>
          </div>
          <button type="button" class="link caption" :aria-expanded="showFilter" @click="showFilter = !showFilter">{{ showFilter ? 'Ẩn bộ lọc' : 'Lọc theo đường dẫn…' }}</button>
          <div v-if="showFilter" class="grid2">
            <label class="lbl"><span class="section-label">Chỉ lấy (glob, phẩy)</span><input v-model="include" class="field mono" placeholder="Sprites/**, UI/*.png" /></label>
            <label class="lbl"><span class="section-label">Bỏ qua (glob, phẩy)</span><input v-model="exclude" class="field mono" placeholder="Fonts/**" /></label>
          </div>
          <p v-if="scanError" class="err callout" role="alert"><CircleAlert :size="14" /> {{ scanError }}</p>

          <Transition name="fade-up">
            <div v-if="scan" class="scan">
              <div class="kinds">
                <div class="kind total">
                  <span class="big num"><TweenNumber :value="total" /></span>
                  <span class="caption">ảnh sẽ vẽ lại</span>
                </div>
                <div v-for="k in kinds" :key="k" class="kind">
                  <span class="big num"><TweenNumber :value="scan.counts[k]" /></span>
                  <span class="caption">{{ KIND_LABEL[k] }}</span>
                </div>
                <div class="kind">
                  <span class="big num"><TweenNumber :value="scan.units" /></span>
                  <span class="caption">lượt Klein</span>
                </div>
              </div>
              <div class="est callout">
                <span>Ước tính: chạy thử {{ TRIAL }} ảnh <strong class="num">~{{ fmtS(scan.estimate.trial_s) }}</strong> · cả thư mục <strong class="num">~{{ fmtS(scan.estimate.full_s) }}</strong></span>
                <span class="caption">({{ scan.estimate.measured ? `đo từ ${scan.estimate.measured} lượt Klein gần đây` : 'chưa có số đo, ước 30 s/lượt' }}, ~{{ scan.estimate.edit_s }} s/lượt)</span>
              </div>
              <button v-if="scan.skipped_total" type="button" class="link caption" :aria-expanded="skippedOpen" @click="skippedOpen = !skippedOpen">
                Bỏ qua {{ scan.skipped_total }} ảnh (normal map, mask, quá lớn…)
              </button>
              <ul v-if="skippedOpen" class="skipped caption mono">
                <li v-for="s in scan.skipped" :key="s.rel">{{ s.rel }} — {{ s.why }}</li>
              </ul>
            </div>
          </Transition>
        </section>

        <section class="card pane" :class="{ dim: !scan }">
          <div class="pane-head">
            <Paintbrush class="pi" aria-hidden="true" />
            <h2 class="headline">Style đích</h2>
            <span v-if="project" class="caption">Dự án “{{ project.name }}” tự điền style + ảnh tham chiếu nếu để trống</span>
          </div>
          <div class="style-grid">
            <label class="lbl">
              <span class="section-label">Mô tả style (tiếng Anh tốt nhất)</span>
              <textarea v-model="style" class="field" rows="4" placeholder="cozy hand-painted fantasy, soft cel shading, warm palette, thin dark outline" />
            </label>
            <div class="lbl">
              <span class="section-label">Ảnh style tham chiếu (tuỳ chọn)</span>
              <BlobDropZone v-model="styleRef" kind="image" label="Thả ảnh style" />
            </div>
          </div>
          <div class="row">
            <span class="section-label">Alpha</span>
            <div class="seg" role="radiogroup" aria-label="Alpha">
              <button v-for="m in MATTES" :key="m.id" type="button" role="radio" :aria-checked="matte === m.id" :title="m.hint" @click="matte = m.id">{{ m.label }}</button>
            </div>
          </div>
          <div class="actions">
            <button type="button" class="btn btn-primary btn-lg" :disabled="!scan || !!starting" @click="start('trial')">
              <LoaderCircle v-if="starting === 'trial'" class="spin" /><Play v-else /> Chạy thử {{ Math.min(TRIAL, total || TRIAL) }} ảnh
            </button>
            <button type="button" class="btn btn-lg" :disabled="!scan || !!starting" @click="start('full')">
              <LoaderCircle v-if="starting === 'full'" class="spin" /><Play v-else /> Chạy cả lô ({{ total }} ảnh)
            </button>
            <span class="caption safe"><ShieldCheck :size="14" /> Chạy chỉ tạo bản xem trước; không ghi gì vào project cho tới khi bấm “Áp vào project”.</span>
          </div>
        </section>

        <Transition name="fade-up">
          <section v-if="run" class="card pane">
            <div class="pane-head">
              <h2 class="headline">{{ isTrial ? 'Chạy thử' : 'Cả lô' }}</h2>
              <RunStatusBadge :status="run.status" />
              <span v-if="eta !== null" class="caption num">còn ~{{ fmtS(eta) }}</span>
              <button type="button" class="btn btn-plain push" @click="openRun"><ExternalLink /> Chi tiết lượt chạy</button>
            </div>
            <div class="bar" role="progressbar" :aria-valuenow="Math.round(overall * 100)" aria-valuemin="0" aria-valuemax="100">
              <span :style="{ transform: `scaleX(${overall})` }" :class="{ live: active }" />
            </div>
            <ol class="pipe">
              <li v-for="s in STEPS" :key="s.key" :class="`st-${stepByKey.get(s.key)?.status ?? 'pending'}`">
                <span class="dot" aria-hidden="true" />
                <span class="pl">{{ s.label }}</span>
                <span v-if="stepCount(stepByKey.get(s.key))" class="num caption">
                  <TweenNumber :value="stepCount(stepByKey.get(s.key))!.done" />/{{ stepCount(stepByKey.get(s.key))!.max }}
                </span>
              </li>
            </ol>
            <p v-if="run.error" class="err callout"><CircleAlert :size="14" /> {{ run.error }}</p>

            <div v-if="gateStep" class="gate">
              <span class="callout">Xem bảng trước/sau rồi duyệt. Duyệt xong mới bấm được “Áp vào project”.</span>
              <button type="button" class="btn btn-primary" :disabled="resolving" @click="resolve(true)"><Check /> Duyệt</button>
              <button type="button" class="btn" :disabled="resolving" @click="resolve(false)"><X /> Từ chối</button>
            </div>

            <div v-if="report" class="summary">
              <span><strong class="num"><TweenNumber :value="reportRows.length" /></strong> ảnh</span>
              <span v-if="qaScore !== null">QA đồng bộ <strong class="num"><TweenNumber :value="qaScore" /></strong>/100</span>
              <span v-if="report.flags.silhouette.length" class="warn">{{ report.flags.silhouette.length }} ảnh đổi silhouette (alpha BiRefNet)</span>
              <span v-if="report.flags.low_qa.length" class="warn">{{ report.flags.low_qa.length }} ảnh QA thấp</span>
            </div>

            <div v-if="report" class="table" role="table" aria-label="Bảng trước / sau">
              <div class="tr th" role="row">
                <span role="columnheader">Trước</span><span role="columnheader">Sau</span><span role="columnheader">Ảnh</span><span role="columnheader">QA</span>
              </div>
              <TransitionGroup name="row" tag="div" class="tbody">
                <div v-for="r in reportRows" :key="r.rel" class="tr" :class="{ flag: flagged.has(r.rel) }" role="row">
                  <span class="cell checker"><BlobImage :hash="r.src" :size="128" :alt="`${r.rel} trước`" /></span>
                  <span class="cell checker"><BlobImage :hash="r.new_hash" :size="128" :alt="`${r.rel} sau`" /></span>
                  <span class="meta">
                    <span class="mono rel">{{ r.rel }}</span>
                    <span class="caption">{{ KIND_LABEL[r.kind] }} · {{ r.width }}×{{ r.height }}<template v-if="r.what"> · {{ r.what }}</template></span>
                    <span class="caption">
                      alpha {{ r.matte === 'original' ? 'gốc' : 'BiRefNet' }}<template v-if="r.slice_fixed.length"> · giữ {{ r.slice_fixed.join(', ') }}</template>
                      <template v-if="r.guid"> · GUID {{ r.guid.slice(0, 10) }}…</template>
                    </span>
                    <span v-for="(i, k) in r.qa.issues.slice(0, 2)" :key="k" class="caption warn">{{ i.message }}</span>
                  </span>
                  <span class="qa num" :class="`g-${r.qa.grade}`">{{ r.qa.score ?? '–' }}</span>
                </div>
              </TransitionGroup>
            </div>

            <div v-if="report && !isTrial" class="apply">
              <template v-if="!confirming">
                <button type="button" class="btn btn-primary btn-lg" :disabled="!canApply || applying || !!liveBackup" @click="confirming = true">
                  <Upload /> Áp vào project
                </button>
                <span v-if="!canApply && run.status !== 'succeeded'" class="caption">Duyệt xong lượt chạy để áp.</span>
              </template>
              <div v-else class="confirm callout" role="alertdialog" aria-label="Xác nhận ghi đè">
                <span>Ghi đè <strong>{{ reportRows.length }}</strong> PNG trong <span class="mono">{{ report.root }}</span>. Ảnh gốc được nén vào backup zip trước; .meta/.import không đổi.</span>
                <button type="button" class="btn btn-primary" :disabled="applying" @click="applyNow"><LoaderCircle v-if="applying" class="spin" /><Check v-else /> Ghi đè</button>
                <button type="button" class="btn" :disabled="applying" @click="confirming = false">Huỷ</button>
              </div>
              <p v-if="applied" class="callout ok">
                Đã ghi {{ applied.written }} ảnh, bỏ qua {{ applied.skipped }}.
                <template v-if="applied.importers_unchanged !== undefined">.meta/.import {{ applied.importers_unchanged ? 'không đổi byte nào' : 'CÓ thay đổi — kiểm tra lại!' }}.</template>
              </p>
              <ul v-if="applied" class="caption mono skipped">
                <li v-for="f in applied.files.filter((x) => x.action === 'skip')" :key="f.rel">{{ f.rel }} — {{ f.why }}</li>
              </ul>
              <div v-for="b in backups" :key="b.backup_id" class="backup caption">
                <span class="mono">{{ b.backup_id }}</span>
                <span>{{ b.written }} ảnh</span>
                <span v-if="b.undone_at">đã hoàn tác</span>
                <button v-else type="button" class="btn" :disabled="undoing === b.backup_id" @click="undo(b.backup_id)">
                  <LoaderCircle v-if="undoing === b.backup_id" class="spin" /><Undo2 v-else /> Hoàn tác
                </button>
              </div>
            </div>
            <p v-else-if="report && isTrial && run.status === 'succeeded'" class="callout">Ổn rồi thì bấm “Chạy cả lô”; chưa ưng thì sửa mô tả style / ảnh tham chiếu rồi chạy thử lại.</p>
          </section>
        </Transition>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wrap {
  max-width: 980px;
}
.pane {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
  transition: opacity var(--t-base) var(--ease-out);
}
.pane.dim {
  opacity: 0.55;
}
.pane-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.pi {
  width: 16px;
  height: 16px;
  color: var(--accent);
}
.push {
  margin-left: auto;
}
.tag {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  background: var(--bg-active);
}
.e-unity {
  background: var(--accent-soft);
  color: var(--accent);
}
.e-godot {
  background: var(--st-cached-soft);
  color: var(--st-cached);
}
.folder-row {
  display: flex;
  gap: var(--sp-2);
}
.folder-row .field {
  flex: 1;
}
.link {
  justify-self: start;
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
}
.grid2,
.style-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}
.lbl {
  display: grid;
  gap: 4px;
}
.err {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
}
.scan {
  display: grid;
  gap: var(--sp-2);
}
.kinds {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.kind {
  display: grid;
  min-width: 96px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-content);
  box-shadow: var(--hairline);
}
.kind.total {
  background: var(--accent-soft);
}
.big {
  font-size: var(--fs-title1);
  font-weight: 650;
  letter-spacing: -0.02em;
}
.est {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
}
.skipped {
  max-height: 160px;
  overflow: auto;
  margin: 0;
  padding-left: 18px;
}
.row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.safe {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--st-ok);
}
.bar {
  position: relative;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-active);
}
.bar span {
  position: absolute;
  inset: 0;
  transform-origin: left;
  background: var(--st-running);
  transition: transform 520ms var(--ease-out);
}
.bar span.live {
  background-image: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.35), transparent);
  background-size: 200% 100%;
  animation: sheen 1.6s linear infinite;
  background-color: var(--st-running);
}
@keyframes sheen {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}
.pipe {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.pipe li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-callout);
  color: var(--text-3);
  transition: color var(--t-base);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--st-pending);
  transition: background-color var(--t-base), transform var(--t-base) var(--ease-spring);
}
.st-running,
.st-waiting_resource {
  color: var(--text);
}
.st-running .dot {
  background: var(--st-running);
  transform: scale(1.25);
  animation: pulse 1.2s ease-in-out infinite;
}
.st-waiting_resource .dot {
  background: var(--st-waiting);
}
.st-succeeded,
.st-cached {
  color: var(--text-2);
}
.st-succeeded .dot,
.st-cached .dot {
  background: var(--st-ok);
}
.st-awaiting_human {
  color: var(--st-human);
  font-weight: 600;
}
.st-awaiting_human .dot {
  background: var(--st-human);
}
.st-failed .dot {
  background: var(--st-fail);
}
@keyframes pulse {
  50% {
    opacity: 0.45;
  }
}
.gate,
.apply,
.confirm,
.backup {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.gate {
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--st-human-soft);
}
.confirm {
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--st-waiting-soft);
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-4);
  font-size: var(--fs-callout);
}
.warn {
  color: var(--st-waiting);
}
.ok {
  color: var(--st-ok);
}
.table {
  display: grid;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
  background: var(--bg-content);
}
.tbody {
  display: grid;
  max-height: 560px;
  overflow: auto;
}
.tr {
  display: grid;
  grid-template-columns: 96px 96px 1fr 48px;
  align-items: center;
  gap: var(--sp-3);
  padding: 6px 10px;
  border-top: 0.5px solid var(--separator);
}
.tr.th {
  border-top: 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
  font-weight: 600;
}
.tr.flag {
  background: var(--st-waiting-soft);
}
.cell {
  width: 96px;
  height: 96px;
  border-radius: var(--r-sm);
  overflow: hidden;
  display: grid;
  place-items: center;
}
.cell :deep(img) {
  max-width: 100%;
  max-height: 100%;
  image-rendering: pixelated;
}
.meta {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.rel {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.qa {
  justify-self: end;
  font-weight: 650;
}
.g-ok {
  color: var(--st-ok);
}
.g-warn {
  color: var(--st-waiting);
}
.g-bad {
  color: var(--st-fail);
}
.spin {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.fade-up-enter-active,
.fade-up-leave-active {
  transition: opacity var(--t-base) var(--ease-out), transform var(--t-base) var(--ease-out);
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
.row-enter-active {
  transition: opacity var(--t-base) var(--ease-out), transform var(--t-base) var(--ease-out);
}
.row-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
@media (prefers-reduced-motion: reduce) {
  .bar span,
  .bar span.live,
  .st-running .dot,
  .spin {
    animation: none;
    transition: none;
  }
}
</style>
