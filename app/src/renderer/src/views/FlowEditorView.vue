<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { onClickOutside, onKeyStroke, useDebounceFn } from '@vueuse/core'
import type { Connection } from '@vue-flow/core'
import { ArrowLeft, CircleAlert, CircleCheck, LoaderCircle, Plus, Save, Search, ShieldCheck, Trash2, TriangleAlert } from '@lucide/vue'
import type { FlowIssue, RecipeInfo, WorkflowDef, WorkflowInputSpec, WorkflowNodeDef } from '@/api/types'
import { ApiError } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import FlowCanvas from '@/components/flow/FlowCanvas.vue'
import { buildFlowEditGraph } from '@/composables/flowGraph'
import { flowRows, formatSource, isBlobType, issueText, nodeIssues, parseSource, slugId, WINPUTS_NODE, WORKFLOW_REF, wouldCycle } from '@/composables/flows'
import { useUnsavedChanges } from '@/composables/unsaved'
import { useFlows } from '@/stores/flows'
import { useRecipes } from '@/stores/recipes'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ id: string }>()
const flows = useFlows()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const route = useRoute()
const router = useRouter()

const blankDef = (): WorkflowDef => ({ id: '', title: '', description: '', inputs: {}, nodes: [], tags: [], source: 'user' })
const doc = reactive<WorkflowDef>(blankDef())
const isNew = computed(() => props.id === 'new')
const loaded = ref(false)
const loadError = ref<string | null>(null)
const baseline = ref('')
const idTouched = ref(false)
const selected = ref<string | null>(null)
const issues = ref<FlowIssue[]>([])
const positions = reactive<Record<string, { x: number; y: number }>>({})
const saving = ref(false)
const checking = ref(false)

const plain = (): WorkflowDef => JSON.parse(JSON.stringify(doc)) as WorkflowDef
const snapshot = (): string => JSON.stringify({ ...doc, source: undefined })
const dirty = computed(() => loaded.value && snapshot() !== baseline.value)
useUnsavedChanges(dirty)
onBeforeRouteLeave((to) => {
  if (!dirty.value || saving.value || (to.name === 'flow-edit' && to.params.id === doc.id)) return true
  return window.confirm('Workflow có thay đổi chưa lưu. Rời đi và bỏ thay đổi?')
})

function setDoc(d: WorkflowDef): void {
  const copy = structuredClone(d)
  Object.assign(doc, { ...blankDef(), ...copy, nodes: copy.nodes.map((n) => ({ ...n, inputs: n.inputs ?? {}, map: n.map ?? {}, params: n.params ?? {} })) })
  for (const k of Object.keys(positions)) delete positions[k]
  selected.value = null
  issues.value = []
  baseline.value = snapshot()
}

async function load(): Promise<void> {
  loaded.value = false
  loadError.value = null
  try {
    if (isNew.value) {
      const from = typeof route.query.from === 'string' ? route.query.from : null
      if (from) {
        const src = await api().getFlow(from)
        setDoc({ ...src, id: `${src.id}-copy`, title: `${src.title} (bản sao)`, source: 'user' })
        idTouched.value = true
        baseline.value = ''
      } else {
        setDoc(blankDef())
        idTouched.value = false
      }
    } else {
      setDoc(await api().getFlow(props.id))
      idTouched.value = true
    }
    loaded.value = true
    void validate(false)
  } catch (e) {
    loadError.value = (e as Error).message
  }
}
watch(
  [() => props.id, () => conn.transport],
  ([, t]) => {
    if (!t) return
    void recipes.load()
    void load()
  },
  { immediate: true }
)
watch(
  () => doc.title,
  (t) => {
    if (isNew.value && !idTouched.value) doc.id = slugId(t)
  }
)

const graph = computed(() =>
  buildFlowEditGraph(doc, {
    recipes: recipes.byId,
    outputsOf: (rid) => recipes.byId(rid)?.outputs ?? [],
    issues: issues.value,
    selected: selected.value,
    positions
  })
)
const fitKey = computed(() => `${doc.nodes.length}:${Object.keys(doc.inputs).length}:${flowRows(doc.nodes).map((r) => r.length).join(',')}`)

const node = computed(() => doc.nodes.find((n) => n.id === selected.value) ?? null)
const nodeRecipe = computed(() => (node.value ? recipes.byId(node.value.recipe_id) : undefined))

let validateSeq = 0
async function validate(announce: boolean): Promise<void> {
  const seq = ++validateSeq
  checking.value = announce
  try {
    const res = await api().validateFlow(doc.id || 'new', plain())
    if (seq !== validateSeq) return
    issues.value = res.issues
    if (announce) {
      const errs = res.issues.filter((i) => i.level === 'error').length
      if (errs) toasts.push(`Có ${errs} lỗi cần sửa`, 'error', 3500)
      else toasts.push(res.issues.length ? 'Hợp lệ, còn vài cảnh báo' : 'Workflow hợp lệ', 'success', 2500)
    }
  } catch (e) {
    if (announce) toasts.error(e, 'Không kiểm tra được')
  } finally {
    if (seq === validateSeq) checking.value = false
  }
}
const autoValidate = useDebounceFn(() => void validate(false), 700)
watch(
  () => (loaded.value ? snapshot() : ''),
  (s) => s && autoValidate()
)

const errorCount = computed(() => issues.value.filter((i) => i.level === 'error').length)
const warnCount = computed(() => issues.value.length - errorCount.value)
const nodeIssueList = computed(() => (node.value ? nodeIssues(issues.value, node.value.id) : []))
const generalIssues = computed(() => issues.value.filter((i) => i.node === null || !doc.nodes.some((n) => n.id === i.node)))

function issuesFrom(e: unknown): FlowIssue[] {
  const list = e instanceof ApiError ? e.detail.issues : undefined
  return Array.isArray(list) ? (list as FlowIssue[]) : []
}

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  try {
    const def = plain()
    const saved = isNew.value ? await api().createFlow(def) : await api().saveFlow(def)
    issues.value = saved.issues ?? []
    baseline.value = snapshot()
    doc.source = saved.source
    await flows.loadDefs()
    toasts.push('Đã lưu workflow', 'success', 2500)
    if (isNew.value) await router.replace(`/flows/${encodeURIComponent(saved.id)}/edit`)
  } catch (e) {
    const found = issuesFrom(e)
    if (found.length) issues.value = found
    toasts.error(e, 'Không lưu được')
  } finally {
    saving.value = false
  }
}
onKeyStroke('s', (e) => {
  if (!(e.ctrlKey || e.metaKey)) return
  e.preventDefault()
  void save()
})

// ---- add / remove nodes ----
const picker = ref(false)
const pickerRoot = ref<HTMLElement | null>(null)
const pickerInput = ref<HTMLInputElement | null>(null)
const query = ref('')
onClickOutside(pickerRoot, () => (picker.value = false))
onKeyStroke('Escape', () => (picker.value = false))
async function openPicker(): Promise<void> {
  picker.value = !picker.value
  query.value = ''
  if (picker.value) {
    await nextTick()
    pickerInput.value?.focus()
  }
}
const choices = computed(() => {
  const q = query.value.trim().toLowerCase()
  return [...recipes.list]
    .filter((r) => !q || r.name.toLowerCase().includes(q) || r.id.toLowerCase().includes(q) || r.description.toLowerCase().includes(q))
    .sort((a, b) => a.name.localeCompare(b.name, 'vi'))
})

function uniqueNodeId(base: string): string {
  const root = slugId(base).replace(/-/g, '_').replace(/^(\d)/, '_$1').slice(0, 24) || 'node'
  const taken = new Set(doc.nodes.map((n) => n.id))
  if (!taken.has(root)) return root
  for (let i = 2; ; i++) if (!taken.has(`${root}_${i}`)) return `${root}_${i}`
}

function addNode(r: RecipeInfo): void {
  const n: WorkflowNodeDef = { id: uniqueNodeId(r.id), recipe_id: r.id, title: r.name, inputs: {}, map: {}, params: {} }
  doc.nodes.push(n)
  selected.value = n.id
  picker.value = false
}

function removeNode(id: string): void {
  doc.nodes = doc.nodes.filter((n) => n.id !== id)
  for (const n of doc.nodes) for (const [k, src] of Object.entries(n.map)) if (parseSource(src)?.ref === id) delete n.map[k]
  delete positions[id]
  selected.value = null
}

function changeRecipe(n: WorkflowNodeDef, recipeId: string): void {
  const r = recipes.byId(recipeId)
  n.recipe_id = recipeId
  if (r) {
    for (const k of Object.keys(n.inputs)) if (!(k in r.inputs)) delete n.inputs[k]
    for (const k of Object.keys(n.map)) if (!(k in r.inputs)) delete n.map[k]
  }
}

// ---- wiring ----
function typeOf(n: WorkflowNodeDef, input: string): string {
  return recipes.byId(n.recipe_id)?.inputs[input]?.type ?? 'text'
}

function sourceFor(from: string, out: string, targetType: string): string {
  if (from === WINPUTS_NODE) return formatSource({ ref: WORKFLOW_REF, kind: 'inputs', name: out, index: null })
  return formatSource({ ref: from, kind: 'outputs', name: out, index: targetType === 'images' ? null : 0 })
}

function wire(targetId: string, input: string, from: string, out: string): boolean {
  const target = doc.nodes.find((n) => n.id === targetId)
  if (!target) return false
  const type = typeOf(target, input)
  if (from === WINPUTS_NODE) {
    const spec = doc.inputs[out]
    if (!spec) return false
    if (isBlobType(spec.type) !== isBlobType(type)) {
      toasts.push(`Không khớp kiểu: đầu vào "${out}" là ${spec.type}, "${input}" là ${type}`, 'error', 4000)
      return false
    }
  } else {
    if (!isBlobType(type)) {
      toasts.push(`"${input}" là ${type}, chỉ ô ảnh / file mới nhận kết quả của node khác`, 'error', 4000)
      return false
    }
    if (wouldCycle(doc.nodes, from, targetId)) {
      toasts.push('Nối như vậy sẽ tạo vòng lặp giữa các node', 'error', 4000)
      return false
    }
  }
  target.map[input] = sourceFor(from, out, type)
  delete target.inputs[input]
  return true
}

function onConnect(c: Connection): void {
  const out = c.sourceHandle?.startsWith('o:') ? c.sourceHandle.slice(2) : null
  const input = c.targetHandle?.startsWith('i:') ? c.targetHandle.slice(2) : null
  if (!out || !input || c.source === c.target) return
  wire(c.target, input, c.source, out)
}

function onEdgeClick(id: string): void {
  const [kind, nodeId, input] = id.split('|')
  if (kind !== 'm') return
  delete doc.nodes.find((n) => n.id === nodeId)?.map[input!]
}

function onDragStop(list: Array<{ id: string; x: number; y: number }>): void {
  for (const p of list) positions[p.id] = { x: p.x, y: p.y }
}

interface Option {
  value: string
  label: string
}
function optionsFor(n: WorkflowNodeDef, input: string): { inputs: Option[]; nodes: Array<{ node: string; items: Option[] }> } {
  const type = typeOf(n, input)
  const inputs = Object.entries(doc.inputs)
    .filter(([, s]) => isBlobType(s.type) === isBlobType(type))
    .map(([name, s]) => ({ value: sourceFor(WINPUTS_NODE, name, type), label: s.label || name }))
  const nodes = isBlobType(type)
    ? doc.nodes
        .filter((o) => o.id !== n.id && !wouldCycle(doc.nodes, o.id, n.id))
        .map((o) => ({ node: o.title || o.id, items: (recipes.byId(o.recipe_id)?.outputs ?? []).map((out) => ({ value: sourceFor(o.id, out, type), label: `${o.title || o.id} · ${out}` })) }))
        .filter((g) => g.items.length)
    : []
  return { inputs, nodes }
}
function pickSource(n: WorkflowNodeDef, input: string, value: string): void {
  if (!value) delete n.map[input]
  else {
    n.map[input] = value
    delete n.inputs[input]
  }
}
function knownSource(n: WorkflowNodeDef, input: string): boolean {
  const cur = n.map[input]
  if (!cur) return true
  const o = optionsFor(n, input)
  return o.inputs.some((x) => x.value === cur) || o.nodes.some((g) => g.items.some((x) => x.value === cur))
}

function setFixed(n: WorkflowNodeDef, input: string, spec: { type: string }, raw: string | boolean): void {
  if (raw === '' || raw === false) return void delete n.inputs[input]
  if (spec.type === 'int' || spec.type === 'float') {
    const v = Number(raw)
    if (!Number.isNaN(v)) n.inputs[input] = v
    return
  }
  n.inputs[input] = raw
}

const paramsText = ref('')
const paramsError = ref<string | null>(null)
watch(
  () => node.value?.id,
  () => {
    paramsText.value = node.value && Object.keys(node.value.params).length ? JSON.stringify(node.value.params, null, 2) : ''
    paramsError.value = null
  },
  { immediate: true }
)
function commitParams(): void {
  const n = node.value
  if (!n) return
  if (!paramsText.value.trim()) {
    n.params = {}
    paramsError.value = null
    return
  }
  try {
    const v: unknown = JSON.parse(paramsText.value)
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('not an object')
    n.params = v as Record<string, unknown>
    paramsError.value = null
  } catch {
    paramsError.value = 'Cần một đối tượng JSON, ví dụ {"buoc": {"seed": 7}}'
  }
}

// ---- workflow inputs ----
const INPUT_TYPES: Array<[string, string]> = [
  ['text', 'Chữ'],
  ['int', 'Số nguyên'],
  ['float', 'Số thực'],
  ['bool', 'Bật / tắt'],
  ['preset', 'Preset xuất'],
  ['image', 'Ảnh'],
  ['mesh', 'Mesh 3D'],
  ['file', 'Tệp'],
  ['video', 'Video']
]
function uniqueInputName(): string {
  for (let i = 1; ; i++) if (!(`input${i}` in doc.inputs)) return `input${i}`
}
function addInput(): void {
  const name = uniqueInputName()
  doc.inputs[name] = { type: 'text', label: '', required: true }
}
function removeInput(name: string): void {
  delete doc.inputs[name]
  for (const n of doc.nodes) for (const [k, src] of Object.entries(n.map)) {
    const p = parseSource(src)
    if (p?.ref === WORKFLOW_REF && p.name === name) delete n.map[k]
  }
}
function renameInput(old: string, e: Event): void {
  const el = e.target as HTMLInputElement
  const next = el.value.trim()
  if (!next || next === old || next in doc.inputs) return void (el.value = old)
  const entries = Object.entries(doc.inputs).map(([k, v]) => [k === old ? next : k, v] as const)
  for (const k of Object.keys(doc.inputs)) delete doc.inputs[k]
  for (const [k, v] of entries) doc.inputs[k] = v
  for (const n of doc.nodes) for (const [k, src] of Object.entries(n.map)) {
    const p = parseSource(src)
    if (p?.ref === WORKFLOW_REF && p.name === old) n.map[k] = formatSource({ ...p, name: next })
  }
}
function setInputType(name: string, type: string): void {
  const spec = doc.inputs[name]
  if (!spec) return
  const blobChanged = isBlobType(spec.type) !== isBlobType(type)
  spec.type = type
  spec.default = undefined
  if (blobChanged)
    for (const n of doc.nodes) for (const [k, src] of Object.entries(n.map)) {
      const p = parseSource(src)
      if (p?.ref === WORKFLOW_REF && p.name === name) delete n.map[k]
    }
}
function setDefault(spec: WorkflowInputSpec, raw: string): void {
  if (raw === '') spec.default = undefined
  else spec.default = spec.type === 'int' || spec.type === 'float' ? Number(raw) : raw
}
const needsDefault = (t: string): boolean => !isBlobType(t) && t !== 'bool' && t !== 'preset'

function focusIssue(i: FlowIssue): void {
  if (i.node && doc.nodes.some((n) => n.id === i.node)) selected.value = i.node
  else if (i.field?.startsWith('inputs.')) selected.value = WINPUTS_NODE
  else selected.value = null
}
const showWorkflowPanel = computed(() => !node.value)
</script>

<template>
  <div class="view">
    <PageHeader :title="doc.title || (isNew ? 'Workflow mới' : 'Sửa workflow')" :subtitle="doc.source === 'builtin' ? 'Bản gốc: lưu sẽ tạo bản riêng của bạn' : 'Kéo thả để nối các quy trình'">
      <template #leading>
        <RouterLink to="/flows" class="btn btn-plain btn-icon" aria-label="Về danh sách workflow"><ArrowLeft /></RouterLink>
      </template>
      <span v-if="errorCount || warnCount" class="count-chip" :class="{ bad: errorCount }" role="status">
        <CircleAlert v-if="errorCount" :size="14" />
        <TriangleAlert v-else :size="14" />
        {{ errorCount ? `${errorCount} lỗi` : `${warnCount} cảnh báo` }}
      </span>
      <div ref="pickerRoot" class="add">
        <button type="button" class="btn" aria-haspopup="listbox" :aria-expanded="picker" aria-label="Thêm node" title="Thêm node" @click="openPicker"><Plus /> <span class="lbl">Thêm node</span></button>
        <div v-if="picker" class="picker card" role="listbox" aria-label="Chọn quy trình">
          <label class="search"><Search :size="14" /><input ref="pickerInput" v-model="query" type="search" placeholder="Tìm quy trình" aria-label="Tìm quy trình" /></label>
          <ul>
            <li v-for="r in choices" :key="r.id">
              <button type="button" role="option" class="opt" @click="addNode(r)">
                <span class="o-name">{{ r.name }}</span>
                <span class="caption o-desc">{{ r.description }}</span>
              </button>
            </li>
            <li v-if="!choices.length" class="caption none">Không có quy trình phù hợp</li>
          </ul>
        </div>
      </div>
      <button type="button" class="btn" :disabled="checking || !loaded" aria-label="Kiểm tra" title="Kiểm tra workflow" @click="validate(true)">
        <LoaderCircle v-if="checking" class="spin" />
        <ShieldCheck v-else />
        <span class="lbl">Kiểm tra</span>
      </button>
      <button type="button" class="btn btn-primary" :disabled="saving || !loaded" aria-label="Lưu" title="Lưu" @click="save">
        <LoaderCircle v-if="saving" class="spin" />
        <Save v-else />
        <span class="lbl">Lưu</span>
      </button>
    </PageHeader>

    <EmptyState v-if="loadError" :icon="CircleAlert" title="Không mở được workflow" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>
    <div v-else-if="loaded" class="editor">
      <section class="canvas-wrap" aria-label="Sơ đồ workflow">
        <FlowCanvas
          id="flow-editor"
          editable
          :nodes="graph.nodes"
          :edges="graph.edges"
          :fit-key="fitKey"
          :minimap="false"
          @connect="onConnect"
          @node-click="(id) => (selected = id)"
          @edge-click="onEdgeClick"
          @pane-click="selected = null"
          @drag-stop="onDragStop"
        />
        <p v-if="!doc.nodes.length" class="hint-empty">Bấm "Thêm node" để chọn quy trình đầu tiên.</p>
        <p v-else class="hint caption">Kéo từ chấm bên phải sang chấm bên trái để nối. Bấm vào dây để gỡ.</p>
      </section>

      <aside class="inspector scroll" aria-label="Thuộc tính">
        <template v-if="node">
          <header class="i-head">
            <h2 class="title-2">Node</h2>
            <button type="button" class="btn btn-plain danger" @click="removeNode(node.id)"><Trash2 /> Xoá node</button>
          </header>
          <div class="f">
            <label for="n-title" class="f-label">Tên hiển thị</label>
            <input id="n-title" v-model="node.title" class="field" type="text" />
          </div>
          <div class="f">
            <label for="n-recipe" class="f-label">Quy trình</label>
            <select id="n-recipe" class="field" :value="node.recipe_id" @change="changeRecipe(node, ($event.target as HTMLSelectElement).value)">
              <option v-if="!nodeRecipe" :value="node.recipe_id">{{ node.recipe_id }} (không còn)</option>
              <option v-for="r in [...recipes.list].sort((a, b) => a.name.localeCompare(b.name, 'vi'))" :key="r.id" :value="r.id">{{ r.name }}</option>
            </select>
            <p v-if="!nodeRecipe && recipes.loaded" class="caption err">Quy trình này đã bị xoá. Chọn quy trình khác hoặc xoá node.</p>
            <p class="caption">Mã node: <code>{{ node.id }}</code></p>
          </div>

          <ul v-if="nodeIssueList.length" class="issue-list" role="alert">
            <li v-for="(i, k) in nodeIssueList" :key="k" :class="i.level">
              <CircleAlert v-if="i.level === 'error'" :size="14" />
              <TriangleAlert v-else :size="14" />
              <span>{{ issueText(i) }}</span>
            </li>
          </ul>

          <h3 class="section-label">Đầu vào của node</h3>
          <p v-if="nodeRecipe && !Object.keys(nodeRecipe.inputs).length" class="caption">Quy trình này không có đầu vào.</p>
          <div v-for="[name, spec] in Object.entries(nodeRecipe?.inputs ?? {})" :key="name" class="f in-row">
            <div class="f-label">
              {{ spec.label || name }}
              <span v-if="spec.required !== false && spec.default === undefined" class="req" aria-hidden="true">*</span>
              <span class="tag">{{ spec.type }}</span>
            </div>
            <select class="field" :aria-label="`Nguồn của ${spec.label || name}`" :value="node.map[name] ?? ''" @change="pickSource(node, name, ($event.target as HTMLSelectElement).value)">
              <option value="">{{ isBlobType(spec.type) ? 'Chưa nối' : 'Nhập tay' }}</option>
              <option v-if="!knownSource(node, name)" :value="node.map[name]">{{ node.map[name] }}</option>
              <optgroup v-if="optionsFor(node, name).inputs.length" label="Đầu vào workflow">
                <option v-for="o in optionsFor(node, name).inputs" :key="o.value" :value="o.value">{{ o.label }}</option>
              </optgroup>
              <optgroup v-for="g in optionsFor(node, name).nodes" :key="g.node" :label="`Kết quả của ${g.node}`">
                <option v-for="o in g.items" :key="o.value" :value="o.value">{{ o.label }}</option>
              </optgroup>
            </select>
            <template v-if="!node.map[name] && !isBlobType(spec.type)">
              <input
                v-if="spec.type === 'bool'"
                type="checkbox"
                class="chk"
                :checked="node.inputs[name] === true"
                :aria-label="spec.label || name"
                @change="setFixed(node, name, spec, ($event.target as HTMLInputElement).checked)"
              />
              <textarea
                v-else-if="spec.type === 'text'"
                class="field"
                rows="2"
                :value="String(node.inputs[name] ?? '')"
                :placeholder="spec.default !== undefined ? `Mặc định: ${spec.default}` : 'Giá trị cố định'"
                :aria-label="spec.label || name"
                @input="setFixed(node, name, spec, ($event.target as HTMLTextAreaElement).value)"
              />
              <input
                v-else
                class="field"
                :type="spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'"
                :value="String(node.inputs[name] ?? '')"
                :placeholder="spec.default !== undefined ? `Mặc định: ${spec.default}` : 'Giá trị cố định'"
                :aria-label="spec.label || name"
                @input="setFixed(node, name, spec, ($event.target as HTMLInputElement).value)"
              />
            </template>
            <p v-else-if="!node.map[name]" class="caption">Nối từ đầu vào workflow hoặc kết quả của node khác.</p>
          </div>

          <details class="adv">
            <summary>Tham số nâng cao (JSON)</summary>
            <textarea v-model="paramsText" class="field mono" rows="5" spellcheck="false" aria-label="Tham số nâng cao" @blur="commitParams" />
            <p v-if="paramsError" class="caption err">{{ paramsError }}</p>
          </details>
        </template>

        <template v-else-if="showWorkflowPanel">
          <h2 class="title-2">Workflow</h2>
          <div class="f">
            <label for="w-title" class="f-label">Tên</label>
            <input id="w-title" v-model="doc.title" class="field" type="text" placeholder="Ví dụ: Concept sang sprite 8 hướng" />
          </div>
          <div class="f">
            <label for="w-id" class="f-label">Mã</label>
            <input id="w-id" v-model="doc.id" class="field mono" type="text" :disabled="!isNew" @input="idTouched = true" />
            <p class="caption">{{ isNew ? 'Chữ không dấu, số, gạch ngang. Không đổi được sau khi lưu.' : 'Mã không đổi được.' }}</p>
          </div>
          <div class="f">
            <label for="w-desc" class="f-label">Mô tả</label>
            <textarea id="w-desc" v-model="doc.description" class="field" rows="3" />
          </div>

          <header class="i-head">
            <h3 class="section-label">Đầu vào workflow</h3>
            <button type="button" class="btn btn-plain" @click="addInput"><Plus /> Thêm</button>
          </header>
          <p v-if="!Object.keys(doc.inputs).length" class="caption">Đầu vào là những gì người chạy phải nhập, ví dụ mô tả nhân vật hoặc ảnh tham chiếu. Nối chúng vào ô của các node.</p>
          <div v-for="(spec, name) in doc.inputs" :key="name" class="input-card card">
            <div class="row">
              <input class="field mono" type="text" :value="name" aria-label="Tên đầu vào" @change="renameInput(String(name), $event)" />
              <select class="field" :value="spec.type" aria-label="Kiểu" @change="setInputType(String(name), ($event.target as HTMLSelectElement).value)">
                <option v-for="[t, l] in INPUT_TYPES" :key="t" :value="t">{{ l }}</option>
              </select>
              <button type="button" class="btn btn-plain btn-icon" :aria-label="`Xoá đầu vào ${name}`" title="Xoá đầu vào" @click="removeInput(String(name))"><Trash2 /></button>
            </div>
            <input v-model="spec.label" class="field" type="text" placeholder="Nhãn hiển thị" aria-label="Nhãn hiển thị" />
            <div class="row">
              <input v-if="needsDefault(spec.type)" class="field" :type="spec.type === 'text' ? 'text' : 'number'" :value="spec.default === undefined || spec.default === null ? '' : String(spec.default)" placeholder="Giá trị mặc định" aria-label="Giá trị mặc định" @input="setDefault(spec, ($event.target as HTMLInputElement).value)" />
              <label class="check"><input v-model="spec.required" type="checkbox" class="chk" /> Bắt buộc</label>
            </div>
          </div>
        </template>

        <ul v-if="generalIssues.length" class="issue-list" role="alert">
          <li v-for="(i, k) in generalIssues" :key="k" :class="i.level">
            <button type="button" class="issue-btn" @click="focusIssue(i)">
              <CircleAlert v-if="i.level === 'error'" :size="14" />
              <TriangleAlert v-else :size="14" />
              <span>{{ issueText(i) }}</span>
            </button>
          </li>
        </ul>
        <p v-else-if="loaded && doc.nodes.length && !issues.length" class="ok caption"><CircleCheck :size="14" /> Chưa phát hiện lỗi.</p>
      </aside>
    </div>
    <p v-else class="caption pad">Đang tải…</p>
  </div>
</template>

<style scoped>
.view {
  height: 100%;
}
.editor {
  flex: 1;
  min-height: 0;
  display: flex;
}
.canvas-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  border-right: 0.5px solid var(--separator);
}
.hint-empty,
.hint {
  position: absolute;
  left: 16px;
  right: 88px;
  bottom: 12px;
  margin: 0;
  pointer-events: none;
}
.hint-empty {
  right: auto;
  left: 50%;
  top: 50%;
  bottom: auto;
  transform: translate(-50%, -50%);
  font-size: var(--fs-body);
  color: var(--text-2);
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
.i-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.f {
  display: grid;
  gap: 6px;
}
.f-label {
  font-weight: 600;
}
.in-row {
  padding: 10px;
  border-radius: var(--r-md);
  background: var(--bg-group);
  box-shadow: var(--hairline);
}
.tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: var(--fs-caption);
  font-family: var(--mono);
  color: var(--text-3);
  background: var(--bg-sunken);
}
.req {
  color: var(--st-fail);
}
.err {
  color: var(--st-fail);
}
.ok {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--st-ok);
}
.mono {
  font-family: var(--mono);
}
.danger {
  color: var(--danger);
}
.chk {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.input-card {
  display: grid;
  gap: 8px;
  padding: 10px;
}
.adv summary {
  cursor: pointer;
  color: var(--text-2);
  margin-bottom: 8px;
}
.issue-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.issue-list li {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  overflow-wrap: anywhere;
}
.issue-list li.error {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.issue-list li.warning {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.issue-list svg {
  flex: none;
  margin-top: 2px;
}
.issue-btn {
  display: flex;
  gap: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.count-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.count-chip.bad {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.add {
  position: relative;
}
.picker {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 20;
  width: 360px;
  padding: 8px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3, var(--shadow-2)), var(--hairline);
}
.search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  height: 30px;
  border-radius: var(--r-sm);
  background: var(--bg-control);
  box-shadow: inset 0 0 0 0.5px var(--separator-strong);
}
.search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: var(--fs-body);
}
.picker ul {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  max-height: 320px;
  overflow: auto;
}
.opt {
  display: grid;
  width: 100%;
  gap: 2px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: none;
  text-align: left;
  cursor: pointer;
}
.opt:hover,
.opt:focus-visible {
  background: var(--bg-active);
  outline: none;
}
.o-name {
  font-weight: 600;
}
.o-desc {
  overflow-wrap: anywhere;
}
.none {
  padding: 8px 10px;
}
</style>
