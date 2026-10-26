<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { onKeyStroke, useDebounceFn } from '@vueuse/core'
import type { Connection } from '@vue-flow/core'
import { ArrowLeft, CircleAlert, CircleCheck, LoaderCircle, LayoutGrid, RotateCcw, Save, Trash2, TriangleAlert } from '@lucide/vue'
import type { RecipeDoc, RecipeIssue, RecipeSource, RecipeStepDoc } from '@/api/types'
import { ApiError } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import FlowCanvas from '@/components/flow/FlowCanvas.vue'
import AddStepMenu from '@/components/flow/AddStepMenu.vue'
import StepInspector from '@/components/flow/StepInspector.vue'
import RecipeInspector from '@/components/flow/RecipeInspector.vue'
import {
  buildEditorGraph,
  INPUTS_NODE,
  NEW_PORT,
  OUTPUTS_NODE,
  parts,
  removeStep,
  renameStep,
  stepDeps,
  uniqueStepId
} from '@/composables/graph'
import { api, useConnection } from '@/stores/connection'
import { useKinds } from '@/stores/catalog'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'
import ResizeHandle from '@/components/ResizeHandle.vue'
import { usePaneWidth } from '@/composables/paneSize'
import { useUnsavedChanges } from '@/composables/unsaved'

const props = defineProps<{ id: string }>()
const route = useRoute()
const sideW = usePaneWidth('recipe-inspector', 360)
const router = useRouter()
const conn = useConnection()
const kinds = useKinds()
const recipes = useRecipes()
const toasts = useToasts()

const isNew = computed(() => props.id === 'new')
const doc = ref<RecipeDoc | null>(null)
const savedJson = ref('')
const loadedId = ref('')
const source = ref<RecipeSource | 'new'>('new')
const hasBuiltin = ref(false)
const loadError = ref<string | null>(null)
const issues = shallowRef<RecipeIssue[]>([])
const validating = ref(false)
const saving = ref(false)
const selected = ref<string | null>(null)
const selectedEdge = ref<string | null>(null)
const fitKey = ref('')

const dirty = computed(() => !!doc.value && JSON.stringify(doc.value) !== savedJson.value)
useUnsavedChanges(dirty)
const errors = computed(() => issues.value.filter((i) => i.level === 'error'))
const warnings = computed(() => issues.value.filter((i) => i.level === 'warning'))

const plain = (d: RecipeDoc): RecipeDoc => JSON.parse(JSON.stringify(d)) as RecipeDoc

const BLANK: RecipeDoc = {
  id: 'recipe-moi',
  name: 'Quy trình mới',
  description: '',
  inputs: { prompt: { type: 'text', label: 'Prompt' } },
  steps: [],
  outputs: {}
}

async function load(): Promise<void> {
  loadError.value = null
  try {
    await kinds.load()
    if (isNew.value) {
      const from = typeof route.query.from === 'string' ? route.query.from : ''
      const base = from ? structuredClone((await api().getRecipe(from)).doc) : structuredClone(BLANK)
      if (from) {
        base.id = `${from}-copy`
        base.name = `${base.name ?? from} (bản sao)`
      }
      doc.value = base
      // An untouched new doc (or copy) is not a change worth guarding; Save stays enabled via source === 'new'.
      savedJson.value = JSON.stringify(base)
      source.value = 'new'
      hasBuiltin.value = false
    } else {
      const d = await api().getRecipe(props.id)
      doc.value = d.doc
      savedJson.value = JSON.stringify(d.doc)
      source.value = d.source
      hasBuiltin.value = d.has_builtin
    }
    loadedId.value = doc.value.id
    selected.value = null
    selectedEdge.value = null
    fitKey.value = `${props.id}:${Date.now()}`
    void validate()
  } catch (e) {
    loadError.value = (e as Error).message
  }
}

watch(
  () => [conn.transport, props.id] as const,
  ([t]) => t && void load(),
  { immediate: true }
)

async function validate(): Promise<void> {
  if (!doc.value) return
  validating.value = true
  try {
    issues.value = (await api().validateRecipe({ doc: plain(doc.value) })).issues
  } catch (e) {
    issues.value = [{ level: 'error', step: null, field: null, message: (e as Error).message }]
  } finally {
    validating.value = false
  }
}
const validateSoon = useDebounceFn(validate, 350)
watch(
  () => (doc.value ? JSON.stringify({ ...doc.value, ui: undefined }) : ''),
  (now, before) => before && now !== before && void validateSoon()
)

const graph = computed(() => {
  if (!doc.value) return { nodes: [], edges: [] }
  const g = buildEditorGraph(doc.value, kinds.byKind, issues.value, selected.value)
  if (selectedEdge.value) for (const e of g.edges) if (e.id === selectedEdge.value) e.class = `${String(e.class ?? '')} picked`
  return g
})

const selStep = computed<RecipeStepDoc | undefined>(() => doc.value?.steps.find((s) => s.id === selected.value))

function reaches(from: string, to: string): boolean {
  const d = doc.value
  if (!d) return false
  const byId = new Map(d.steps.map((s) => [s.id, s]))
  const stack = [from]
  const seen = new Set<string>()
  while (stack.length) {
    const cur = stack.pop()!
    if (cur === to) return true
    if (seen.has(cur)) continue
    seen.add(cur)
    const s = byId.get(cur)
    if (s) stack.push(...stepDeps(s))
  }
  return false
}

function onConnect(c: Connection): void {
  const d = doc.value
  if (!d || !c.sourceHandle?.startsWith('o:') || !c.targetHandle?.startsWith('i:')) return
  const out = c.sourceHandle.slice(2)
  let name = c.targetHandle.slice(2)
  const ref = c.source === INPUTS_NODE ? `inputs.${out}` : `${c.source}.${out}`
  if (c.target === OUTPUTS_NODE) {
    if (c.source === INPUTS_NODE) return toasts.push('Đầu ra phải lấy từ một bước', 'info')
    d.outputs ??= {}
    if (name === NEW_PORT) {
      name = out in d.outputs ? `${c.source}_${out}` : out
      if (Object.keys(d.outputs).length === 0) name = 'final'
    }
    d.outputs[name] = ref
    return
  }
  const step = d.steps.find((s) => s.id === c.target)
  if (!step) return
  if (c.source === c.target || (c.source !== INPUTS_NODE && reaches(c.source, c.target)))
    return toasts.push('Nối này tạo vòng lặp giữa các bước', 'error')
  step.in ??= {}
  if (name === NEW_PORT) {
    name = out
    for (let i = 2; name in step.in; i++) name = `${out}${i}`
  }
  const cur = step.in[name]
  if (cur && parts(cur).includes(ref)) return
  step.in[name] = ref
  selected.value = step.id
  selectedEdge.value = null
}

function deleteEdge(id: string): void {
  const d = doc.value
  if (!d) return
  const [type, a, b, i] = id.split('|')
  if (type === 'o' && d.outputs && a) delete d.outputs[a]
  else if (type === 'b' && a && b) {
    const step = d.steps.find((s) => s.id === a)
    const cur = step?.in?.[b]
    if (!step?.in || !cur) return
    const kept = parts(cur).filter((_, j) => j !== Number(i))
    if (kept.length) step.in[b] = kept.length === 1 ? kept[0]! : kept
    else {
      delete step.in[b]
      if (step.map === b) step.map = null
    }
  } else if (type === 'r') {
    toasts.push('Tham chiếu ${steps…} nằm trong tham số — sửa tham số để bỏ', 'info')
    return
  }
  selectedEdge.value = null
}

function deleteStep(id: string): void {
  if (!doc.value) return
  removeStep(doc.value, id)
  selected.value = null
}

function onRename(to: string): void {
  if (!doc.value || !selStep.value) return
  renameStep(doc.value, selStep.value.id, to)
  selected.value = to
}

function onRenameInput(from: string, to: string): void {
  const d = doc.value
  if (!d?.inputs) return
  const entries = Object.entries(d.inputs).map(([k, v]) => [k === from ? to : k, v] as const)
  d.inputs = Object.fromEntries(entries)
  const swapStr = (v: unknown): unknown => {
    if (typeof v === 'string') return v.replaceAll(`\${inputs.${from}}`, `\${inputs.${to}}`).replaceAll(`\${inputs.${from} |`, `\${inputs.${to} |`)
    if (Array.isArray(v)) return v.map(swapStr)
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, swapStr(x)]))
    return v
  }
  for (const s of d.steps) {
    if (s.params) s.params = swapStr(s.params) as Record<string, unknown>
    if (s.in) for (const [k, b] of Object.entries(s.in)) s.in[k] = Array.isArray(b) ? b.map((p) => (p === `inputs.${from}` ? `inputs.${to}` : p)) : b === `inputs.${from}` ? `inputs.${to}` : b
  }
}

function addStep(kind: string): void {
  const d = doc.value
  if (!d) return
  const id = uniqueStepId(d, kind)
  const step: RecipeStepDoc = { id, kind }
  const info = kinds.byKind.get(kind)
  const prev = selStep.value
  const pos = d.ui?.positions ?? {}
  const node = graph.value.nodes.find((n) => n.id === (prev?.id ?? ''))
  let at: [number, number]
  if (node) at = [node.position.x + 300, node.position.y]
  else {
    const xs = graph.value.nodes.filter((n) => n.id !== OUTPUTS_NODE).map((n) => n.position.x)
    at = [Math.max(0, ...xs) + 300, 0]
  }
  if (prev) {
    const prevInfo = kinds.byKind.get(prev.kind)
    const outName = Object.keys(prevInfo?.out ?? {}).find((k) => k !== '*') ?? Object.keys(prev.in ?? {})[0]
    const inName = Object.keys(info?.in ?? {}).find((k) => k !== '*') ?? (info?.in && '*' in info.in ? outName : undefined)
    if (outName && inName) step.in = { [inName]: `${prev.id}.${outName}` }
  }
  d.steps.push(step)
  d.ui = { ...(d.ui ?? {}), positions: { ...pos, [id]: at } }
  selected.value = id
  selectedEdge.value = null
}

function onDragStop(ps: Array<{ id: string; x: number; y: number }>): void {
  const d = doc.value
  if (!d) return
  const positions = { ...(d.ui?.positions ?? {}) }
  for (const n of graph.value.nodes) positions[n.id] ??= [Math.round(n.position.x), Math.round(n.position.y)]
  for (const p of ps) positions[p.id] = [p.x, p.y]
  d.ui = { ...(d.ui ?? {}), positions }
}

function autoLayout(): void {
  if (!doc.value?.ui) return
  delete doc.value.ui.positions
  fitKey.value = `layout:${Date.now()}`
}

async function save(stayOnPage = true): Promise<boolean> {
  const d = doc.value
  if (!d || saving.value) return false
  saving.value = true
  try {
    const res = await api().saveRecipe(d.id, { doc: plain(d) })
    savedJson.value = JSON.stringify(d)
    issues.value = res.issues ?? []
    source.value = res.source
    hasBuiltin.value = res.has_builtin
    toasts.push(res.source === 'override' ? 'Đã lưu bản của bạn (bản gốc vẫn giữ nguyên)' : 'Đã lưu quy trình', 'success', 2600)
    void recipes.load(true)
    if (stayOnPage && (isNew.value || d.id !== props.id)) {
      loadedId.value = d.id
      await router.replace(`/recipes/${encodeURIComponent(d.id)}`)
    }
    return true
  } catch (e) {
    if (e instanceof ApiError && e.issues.length) issues.value = e.issues
    toasts.error(e, 'Chưa lưu được')
    return false
  } finally {
    saving.value = false
  }
}

const confirmRevert = ref(false)
let revertTimer: number | undefined
async function revert(): Promise<void> {
  if (!confirmRevert.value) {
    confirmRevert.value = true
    revertTimer = window.setTimeout(() => (confirmRevert.value = false), 3000)
    return
  }
  window.clearTimeout(revertTimer)
  confirmRevert.value = false
  try {
    const res = await api().deleteRecipe(props.id)
    void recipes.load(true)
    if (res.restored_builtin) {
      toasts.push('Đã khôi phục bản gốc', 'success')
      savedJson.value = ''
      await load()
    } else {
      toasts.push('Đã xoá quy trình', 'info')
      savedJson.value = doc.value ? JSON.stringify(doc.value) : ''
      await router.push('/recipes')
    }
  } catch (e) {
    toasts.error(e, 'Không thực hiện được')
  }
}

function typingTarget(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
}
onKeyStroke(['Delete', 'Backspace'], (e) => {
  if (typingTarget(e) || leaveAsk.value) return
  if (selectedEdge.value) deleteEdge(selectedEdge.value)
  else if (selStep.value) deleteStep(selStep.value.id)
})
onKeyStroke('s', (e) => {
  if (!(e.ctrlKey || e.metaKey) || leaveAsk.value) return
  e.preventDefault()
  void save()
})

const leaveAsk = shallowRef<((leave: boolean) => void) | null>(null)
const sheetSave = ref<HTMLButtonElement | null>(null)
const sheetStay = ref<HTMLButtonElement | null>(null)
onBeforeRouteLeave((to) => {
  if (!dirty.value || saving.value) return true
  if (to.params.id && to.params.id === doc.value?.id) return true
  leaveAsk.value?.(false)
  return new Promise<boolean>((resolve) => {
    leaveAsk.value = resolve
    void nextTick(() => (sheetSave.value && !sheetSave.value.disabled ? sheetSave.value : sheetStay.value)?.focus())
  })
})
function answerLeave(leave: boolean): void {
  const resolve = leaveAsk.value
  leaveAsk.value = null
  resolve?.(leave)
}
async function saveAndLeave(): Promise<void> {
  if (await save(false)) answerLeave(true)
}
function onSheetKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.preventDefault()
    answerLeave(false)
  } else if (e.key === 'Tab') {
    const btns = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
    const i = btns.indexOf(document.activeElement as HTMLButtonElement)
    const next = btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length]
    if (next) {
      e.preventDefault()
      next.focus()
    }
  }
}

const sourceLabel: Record<RecipeSource | 'new', string> = {
  builtin: 'Bản gốc',
  user: 'Quy trình của bạn',
  override: 'Bản của bạn (đè bản gốc)',
  new: 'Chưa lưu'
}
const subtitle = computed(() => (doc.value ? `${doc.value.id} · ${sourceLabel[source.value]}${dirty.value ? ' · có thay đổi' : ''}` : ''))
</script>

<template>
  <div class="view">
    <PageHeader :title="doc?.name || doc?.id || 'Quy trình'" :subtitle="subtitle">
      <template #leading>
        <RouterLink to="/recipes" class="btn btn-plain btn-icon" aria-label="Về danh sách quy trình"><ArrowLeft /></RouterLink>
      </template>
      <span class="vstate" :class="{ bad: errors.length, warn: !errors.length && warnings.length }" aria-live="polite">
        <LoaderCircle v-if="validating" class="spin" :size="14" />
        <CircleAlert v-else-if="errors.length" :size="14" />
        <TriangleAlert v-else-if="warnings.length" :size="14" />
        <CircleCheck v-else :size="14" />
        {{ errors.length ? `${errors.length} lỗi` : warnings.length ? `${warnings.length} cảnh báo` : 'Hợp lệ' }}
      </span>
      <AddStepMenu v-if="doc" :kinds="kinds.list" @pick="addStep" />
      <button v-if="source === 'override' || source === 'user'" class="btn" :class="{ 'btn-danger': confirmRevert }" @click="revert">
        <component :is="source === 'override' ? RotateCcw : Trash2" />
        {{ confirmRevert ? 'Bấm lần nữa' : source === 'override' ? 'Khôi phục gốc' : 'Xoá' }}
      </button>
      <button class="btn btn-primary" :disabled="!doc || saving || (!dirty && source !== 'new') || errors.length > 0" :title="errors.length ? 'Sửa lỗi trước khi lưu' : 'Ctrl + S'" @click="save()">
        <LoaderCircle v-if="saving" class="spin" />
        <Save v-else />
        Lưu
      </button>
    </PageHeader>

    <EmptyState v-if="loadError" :icon="CircleAlert" title="Không mở được quy trình" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>

    <div v-else class="editor">
      <section class="stage" aria-label="Sơ đồ quy trình">
        <FlowCanvas
          v-if="doc"
          id="recipe-editor"
          :nodes="graph.nodes"
          :edges="graph.edges"
          editable
          :focus-id="selected"
          :fit-key="fitKey"
          @connect="onConnect"
          @node-click="(id) => ((selected = id === selected ? null : id), (selectedEdge = null))"
          @edge-click="(id) => ((selectedEdge = id), (selected = null))"
          @pane-click="(selected = null), (selectedEdge = null)"
          @drag-stop="onDragStop"
        />
        <div v-if="doc" class="hud">
          <button class="btn btn-plain hud-btn" @click="autoLayout"><LayoutGrid :size="14" /> Sắp xếp lại</button>
          <span class="caption">Kéo chấm để nối · Delete để xoá · Ctrl+S để lưu</span>
        </div>
        <Transition name="fade">
          <div v-if="selectedEdge" class="edge-bar card">
            <span class="mono">{{ selectedEdge.split('|').slice(1, 3).join(' · ') }}</span>
            <button class="btn btn-danger" @click="deleteEdge(selectedEdge)"><Trash2 /> Bỏ nối</button>
          </div>
        </Transition>
      </section>
      <ResizeHandle v-model="sideW" side="start" label="Bảng thuộc tính" :default-size="360" :min="300" :max="640" />

      <aside class="side scroll" aria-label="Thuộc tính" :style="{ '--pane-w': `${sideW}px` }">
        <Transition name="swap" mode="out-in">
          <StepInspector
            v-if="doc && selStep"
            :key="selStep.id"
            :doc="doc"
            :step="selStep"
            :kinds="kinds.list"
            :issues="issues.filter((i) => i.step === selStep!.id)"
            @rename="onRename"
            @remove="deleteStep(selStep.id)"
            @changed="() => {}"
          />
          <RecipeInspector v-else-if="doc" key="recipe" :doc="doc" :issues="issues" :id-locked="false" @changed="() => {}" @rename-input="onRenameInput" />
        </Transition>
      </aside>
    </div>

    <Teleport to="body">
      <Transition name="sheet">
        <div v-if="leaveAsk" class="sheet-scrim" @mousedown.self="answerLeave(false)">
          <div class="sheet" role="alertdialog" aria-modal="true" aria-labelledby="leave-title" aria-describedby="leave-text" @keydown="onSheetKey">
            <span class="sheet-ic" aria-hidden="true"><TriangleAlert :size="22" /></span>
            <h2 id="leave-title" class="sheet-title">Lưu thay đổi của “{{ doc?.name || doc?.id }}”?</h2>
            <p id="leave-text" class="sheet-text">
              {{ errors.length ? `Quy trình còn ${errors.length} lỗi nên chưa lưu được. Ở lại để sửa, hoặc bỏ thay đổi.` : 'Nếu không lưu, các thay đổi vừa làm sẽ mất.' }}
            </p>
            <div class="sheet-actions">
              <button type="button" class="btn btn-danger" @click="answerLeave(true)">Bỏ thay đổi</button>
              <span class="spacer" />
              <button ref="sheetStay" type="button" class="btn" @click="answerLeave(false)">Ở lại</button>
              <button ref="sheetSave" type="button" class="btn btn-primary" :disabled="errors.length > 0 || saving" @click="saveAndLeave">
                <LoaderCircle v-if="saving" class="spin" />
                Lưu
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.editor {
  flex: 1;
  min-height: 0;
  display: flex;
}
.stage {
  position: relative;
  flex: 1;
  min-width: 0;
  container-type: inline-size;
}
.side {
  width: var(--pane-w, 360px);
  max-width: calc(100% - 280px);
  flex: none;
  border-left: 0.5px solid var(--separator);
  background: var(--bg-content);
  padding: 18px 18px 0;
}
.hud {
  position: absolute;
  left: 14px;
  right: 64px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  pointer-events: none;
}
.hud .caption {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
@container (max-width: 520px) {
  .hud .caption {
    display: none;
  }
}
.hud-btn {
  flex: none;
  pointer-events: auto;
  height: 28px;
  font-size: var(--fs-callout);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
}
.edge-bar {
  position: absolute;
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 6px 6px 14px;
  border-radius: 999px;
  box-shadow: var(--shadow-2), var(--hairline);
}
.mono {
  font-family: var(--mono);
  font-size: 12px;
}
.vstate {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  color: var(--st-ok);
  background: var(--st-ok-soft);
  transition:
    color var(--t-base),
    background-color var(--t-base);
}
.vstate.warn {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.vstate.bad {
  color: var(--st-fail);
  background: var(--st-fail-soft);
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
.sheet-scrim {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 72px 16px 16px;
  background: var(--scrim, rgba(0, 0, 0, 0.4));
}
.sheet {
  width: min(420px, 100%);
  display: grid;
  justify-items: center;
  gap: 8px;
  padding: 22px 20px 16px;
  border-radius: var(--r-xl);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
  text-align: center;
}
.sheet-ic {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin-bottom: 4px;
  border-radius: 50%;
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.sheet-title {
  margin: 0;
  font-size: var(--fs-headline);
  font-weight: 600;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.sheet-text {
  margin: 0;
  font-size: var(--fs-callout);
  line-height: 1.45;
  color: var(--text-2);
}
.sheet-actions {
  justify-self: stretch;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.sheet-actions .spacer {
  flex: 1;
  min-width: 16px;
}
.sheet-actions .btn-primary {
  min-width: 72px;
}
.sheet-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.sheet-enter-active .sheet {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-base) var(--ease-out);
}
.sheet-leave-active {
  transition: opacity var(--t-fast) var(--ease-in);
}
.sheet-enter-from {
  opacity: 0;
}
.sheet-enter-from .sheet {
  opacity: 0;
  transform: translateY(-16px) scale(0.98);
}
.sheet-leave-to {
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
