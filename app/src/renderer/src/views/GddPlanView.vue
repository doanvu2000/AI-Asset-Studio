<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, FileText, LoaderCircle, Play, Plus, Sparkles, X } from '@lucide/vue'
import type { EngineTarget, GddPlan, GddPlanItem } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { api } from '@/stores/connection'
import { useFlows } from '@/stores/flows'
import { useProjects } from '@/stores/projects'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'

const MAX_CHARS = 60000
const STYLE_OWNED = new Set(['style', 'negative', 'preset'])
const BLOB = new Set(['image', 'mesh', 'file', 'video'])
const ENGINES: { id: EngineTarget; label: string }[] = [
  { id: 'none', label: 'Chưa chọn' },
  { id: 'unity', label: 'Unity' },
  { id: 'godot', label: 'Godot' },
  { id: 'flutter', label: 'Flutter' },
  { id: 'android', label: 'Android' },
  { id: 'ios', label: 'iOS' }
]

type Spec = { type: string; default?: unknown; required?: boolean; label?: string }
type Row = GddPlanItem & { on: boolean }

const router = useRouter()
const flows = useFlows()
const recipes = useRecipes()
const projects = useProjects()
const toasts = useToasts()

const text = ref('')
const projectId = ref<string>(projects.activeId ?? NO_PROJECT)
const engine = ref<EngineTarget>('none')
const planning = ref(false)
const starting = ref(false)
const plan = ref<GddPlan | null>(null)
const rows = ref<Row[]>([])
const error = ref<string | null>(null)
const fileInput = ref<HTMLInputElement>()

onMounted(() => {
  void recipes.load()
  if (!flows.defsLoaded) void flows.loadDefs()
  if (!projects.loaded) void projects.load()
  engine.value = projects.byId(projectId.value)?.engine_target ?? 'none'
})

function onProject(): void {
  engine.value = projects.byId(projectId.value)?.engine_target ?? 'none'
}

async function readFile(e: Event): Promise<void> {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  try {
    text.value = (await f.text()).slice(0, MAX_CHARS)
  } catch (err) {
    toasts.error(err, 'Không đọc được tệp')
  }
  if (fileInput.value) fileInput.value.value = ''
}

async function analyze(): Promise<void> {
  planning.value = true
  error.value = null
  try {
    const p = await api().gddPlan({ text: text.value, project_id: projectId.value })
    plan.value = p
    rows.value = p.items.map((i) => ({ ...i, on: !i.missing.length }))
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    planning.value = false
  }
}

function specsOf(kind: GddPlanItem['kind'], id: string): Record<string, Spec> {
  const all: Record<string, Spec> = (kind === 'flow' ? flows.byId(id)?.inputs : recipes.byId(id)?.inputs) ?? {}
  return Object.fromEntries(Object.entries(all).filter(([k]) => !STYLE_OWNED.has(k)))
}

function primaryKey(specs: Record<string, Spec>): string | null {
  for (const k of ['prompt', 'idea']) if (specs[k] && !BLOB.has(specs[k].type)) return k
  return Object.entries(specs).find(([, s]) => s.type === 'text')?.[0] ?? null
}

function missingOf(r: GddPlanItem): string[] {
  return Object.entries(specsOf(r.kind, r.target_id))
    .filter(([k, s]) => s.required !== false && (s.default === undefined || s.default === null || s.default === '') && (r.inputs[k] === undefined || r.inputs[k] === ''))
    .map(([k, s]) => s.label ?? k)
}

function promptOf(r: Row): string {
  const k = primaryKey(specsOf(r.kind, r.target_id))
  return k ? String(r.inputs[k] ?? '') : ''
}

function setPrompt(r: Row, v: string): void {
  const k = primaryKey(specsOf(r.kind, r.target_id))
  if (!k) return
  if (v) r.inputs[k] = v
  else delete r.inputs[k]
  refresh(r)
}

function refresh(r: Row): void {
  r.missing = missingOf(r)
  if (r.missing.length) r.on = false
}

function retarget(r: Row, value: string): void {
  const [kind, id] = value.split(':') as [GddPlanItem['kind'], string]
  const before = promptOf(r)
  const specs = specsOf(kind, id)
  const kept: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(r.inputs)) if (specs[k]) kept[k] = v
  const main = primaryKey(specs)
  if (main && before && kept[main] === undefined) kept[main] = before
  r.kind = kind
  r.target_id = id
  r.inputs = kept
  refresh(r)
}

function addRow(): void {
  const first = flows.defs[0]
  const r: Row = { id: '', name: '', kind: first ? 'flow' : 'recipe', target_id: first?.id ?? recipes.list[0]?.id ?? '', inputs: {}, notes: '', missing: [], on: true }
  refresh(r)
  rows.value.push(r)
}

function targetLabel(r: GddPlanItem): string {
  return (r.kind === 'flow' ? flows.byId(r.target_id)?.title : recipes.byId(r.target_id)?.name) ?? r.target_id
}

const chosen = computed(() => rows.value.filter((r) => r.on))
const blocked = computed(() => chosen.value.filter((r) => !r.name.trim() || !r.target_id || r.missing.length))
const canStart = computed(() => chosen.value.length > 0 && blocked.value.length === 0 && !starting.value)

async function start(): Promise<void> {
  if (!plan.value || !canStart.value) return
  starting.value = true
  try {
    if (projectId.value !== NO_PROJECT && projects.byId(projectId.value)?.engine_target !== engine.value) {
      await projects.patch(projectId.value, { engine_target: engine.value })
    }
    const items = chosen.value.map(({ on: _on, ...item }) => item)
    const res = await api().gddStart(plan.value.id, { items, project_id: projectId.value })
    toasts.push(`Đã tạo ${res.flow_batch_ids.length + (res.recipe_batch_id ? 1 : 0)} lô cho ${items.length} asset`, 'success')
    if (res.flow_batch_ids.length === 1 && !res.recipe_batch_id) void router.push(`/flows/batches/${res.flow_batch_ids[0]}`)
    else if (!res.flow_batch_ids.length && res.recipe_batch_id) void router.push(`/batch/${res.recipe_batch_id}`)
    else void router.push(projectId.value === NO_PROJECT ? '/runs' : `/projects/${projectId.value}`)
  } catch (e) {
    toasts.error(e, 'Không bắt đầu được')
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Từ tài liệu GDD" subtitle="Claude tách danh sách asset và chọn workflow cho từng mục">
      <template #leading>
        <RouterLink to="/new" class="btn btn-plain btn-icon" aria-label="Về Tạo mới"><ArrowLeft /></RouterLink>
      </template>
      <button v-if="plan" class="btn btn-primary" :disabled="!canStart" aria-label="Tạo các lô" title="Tạo các lô" @click="start">
        <LoaderCircle v-if="starting" class="spin" />
        <Play v-else />
        <span class="lbl">Bắt đầu {{ chosen.length }} asset</span>
      </button>
    </PageHeader>

    <div class="scroll">
      <div class="stack">
        <section class="card src" aria-label="Nguồn GDD">
          <div class="src-top">
            <label class="fld">
              <span class="caption">Dự án</span>
              <select v-model="projectId" class="field" :disabled="planning || starting" @change="onProject">
                <option :value="NO_PROJECT">Không gắn dự án</option>
                <option v-for="p in projects.list" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
            </label>
            <label class="fld">
              <span class="caption">Đích engine</span>
              <select v-model="engine" class="field" :disabled="projectId === NO_PROJECT || starting">
                <option v-for="e in ENGINES" :key="e.id" :value="e.id">{{ e.label }}</option>
              </select>
            </label>
            <span class="grow" />
            <input ref="fileInput" type="file" accept=".md,.txt,.markdown,text/plain,text/markdown" hidden @change="readFile" />
            <button class="btn" :disabled="planning" @click="fileInput?.click()"><FileText /> Mở tệp .md / .txt</button>
          </div>
          <textarea v-model="text" class="field gdd" rows="8" :maxlength="MAX_CHARS" placeholder="Dán GDD: thể loại, nhân vật, kẻ địch, vật phẩm, màn chơi, giao diện…" aria-label="Nội dung GDD" :disabled="planning" />
          <div class="src-bottom">
            <span class="caption num">{{ text.length.toLocaleString('vi-VN') }} / {{ MAX_CHARS.toLocaleString('vi-VN') }} ký tự</span>
            <button class="btn btn-primary" :disabled="planning || !text.trim()" @click="analyze">
              <LoaderCircle v-if="planning" class="spin" />
              <Sparkles v-else />
              {{ planning ? 'Đang phân tích…' : plan ? 'Phân tích lại' : 'Phân tích GDD' }}
            </button>
          </div>
          <p v-if="error" class="err" role="alert">{{ error }}</p>
        </section>

        <section v-if="plan" class="card plan" aria-label="Danh sách asset đề xuất">
          <div class="plan-head">
            <h2 class="headline">{{ rows.length }} asset đề xuất</h2>
            <span v-if="plan.cost_usd" class="caption num">${{ plan.cost_usd.toFixed(2) }}</span>
            <span class="grow" />
            <button class="btn" @click="addRow"><Plus /> Thêm mục</button>
          </div>
          <p v-if="plan.dropped.length" class="caption warn">Bỏ {{ plan.dropped.length }} mục Claude gán sai: {{ plan.dropped.join('; ') }}</p>
          <ul class="rows">
            <li v-for="(r, i) in rows" :key="i" class="row" :class="{ off: !r.on }">
              <input v-model="r.on" type="checkbox" class="chk" :disabled="!!r.missing.length" :aria-label="`Chọn ${r.name || 'mục ' + (i + 1)}`" />
              <div class="cell">
                <input v-model="r.name" class="field" maxlength="200" placeholder="Tên asset" :aria-label="`Tên mục ${i + 1}`" />
                <select class="field" :value="`${r.kind}:${r.target_id}`" :aria-label="`Workflow mục ${i + 1}`" @change="retarget(r, ($event.target as HTMLSelectElement).value)">
                  <optgroup label="Workflow (nhiều bước)">
                    <option v-for="d in flows.defs" :key="d.id" :value="`flow:${d.id}`">{{ d.title }}</option>
                  </optgroup>
                  <optgroup label="Công thức lẻ">
                    <option v-for="rc in recipes.list" :key="rc.id" :value="`recipe:${rc.id}`">{{ rc.name }}</option>
                  </optgroup>
                  <option v-if="!flows.byId(r.target_id) && !recipes.byId(r.target_id)" :value="`${r.kind}:${r.target_id}`">{{ targetLabel(r) }}</option>
                </select>
              </div>
              <div class="cell wide">
                <textarea class="field" rows="2" :value="promptOf(r)" placeholder="Mô tả (tiếng Anh)" :aria-label="`Mô tả mục ${i + 1}`" @input="setPrompt(r, ($event.target as HTMLTextAreaElement).value)" />
                <p v-if="r.missing.length" class="caption warn">Cần bổ sung: {{ r.missing.join(', ') }} — gắn tệp ở trang chạy riêng</p>
                <p v-else-if="r.notes" class="caption note">{{ r.notes }}</p>
              </div>
              <button class="btn btn-plain btn-icon" :aria-label="`Bỏ mục ${i + 1}`" title="Bỏ mục" @click="rows.splice(i, 1)"><X /></button>
            </li>
          </ul>
          <p v-if="!rows.length" class="caption empty">Chưa có mục nào. Thêm mục hoặc phân tích lại.</p>
          <p v-if="blocked.length" class="caption warn">{{ blocked.length }} mục đã chọn còn thiếu tên hoặc đầu vào bắt buộc.</p>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.src,
.plan {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
}
.src-top,
.src-bottom,
.plan-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-3);
}
.src-bottom {
  align-items: center;
  justify-content: space-between;
}
.fld {
  display: grid;
  gap: 4px;
  min-width: 180px;
}
.grow {
  flex: 1;
}
.gdd {
  resize: vertical;
  min-height: 140px;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-2);
}
.row {
  display: grid;
  grid-template-columns: auto minmax(180px, 1fr) minmax(220px, 2fr) auto;
  gap: var(--sp-3);
  align-items: start;
  padding: var(--sp-2);
  border-radius: var(--r-md, 8px);
  background: var(--bg-active);
}
.row.off {
  opacity: 0.55;
}
.chk {
  margin-top: 8px;
}
.cell {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.cell textarea {
  resize: vertical;
}
.warn {
  color: var(--st-warn, var(--text-2));
}
.note,
.empty {
  color: var(--text-3);
}
.err {
  color: var(--st-fail);
  overflow-wrap: anywhere;
}
@media (max-width: 760px) {
  .row {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }
  .cell.wide {
    grid-column: 2 / 3;
  }
}
</style>
