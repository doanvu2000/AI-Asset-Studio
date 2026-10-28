<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, BookOpen, FolderKanban, Languages, Layers, LoaderCircle, Play, RotateCcw, RotateCw, Timer } from '@lucide/vue'
import type { BlobRef, RecipeInfo, RecipeInputSpec, StyleBible } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import LaneTag from '@/components/LaneTag.vue'
import QuickPrompts from '@/components/QuickPrompts.vue'
import EmptyState from '@/components/EmptyState.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import PresetSelect from '@/components/PresetSelect.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import { useRecipes } from '@/stores/recipes'
import { useProjects } from '@/stores/projects'
import { usePresets } from '@/stores/catalog'
import { fillSummary, normalizeStyle, styleFills, type StyleFill } from '@/composables/projectStyle'
import { useRuns } from '@/stores/runs'
import { api, useConnection } from '@/stores/connection'
import type { ApiError } from '@/api/client'
import { useToasts } from '@/stores/toasts'
import { stepName } from '@/composables/format'
import { RECIPE_GROUP_LABEL, RECOMMENDED_RECIPES, recipeGroup, recipePitch, type RecipeGroup } from '@/assets/art'
import { QUICK_MATERIALS, quickStyleLabel } from '@/assets/quickPrompts'
import { PREFILL_PREFIX } from '@/composables/followUp'

const recipes = useRecipes()
const projects = useProjects()
const presets = usePresets()
const runs = useRuns()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()
const route = useRoute()

const LAST_KEY = 'studio.newrun.recipe'
function readLast(): string | null {
  try {
    return localStorage.getItem(LAST_KEY)
  } catch {
    return null
  }
}
const lastId = ref(readLast())

watch(
  () => conn.transport,
  (t) => t && void recipes.load(),
  { immediate: true }
)

// Recipe and mode live in the URL so the palette, dashboard and /batch redirect can deep-link here.
const selectedId = computed(() => (typeof route.query.recipe === 'string' ? route.query.recipe : null))
const selected = computed<RecipeInfo | undefined>(() => (selectedId.value ? recipes.byId(selectedId.value) : undefined))
type Mode = 'single' | 'batch'
const mode = computed<Mode>(() => (route.query.mode === 'batch' ? 'batch' : 'single'))

function pick(id: string): void {
  void router.push({ query: { ...route.query, recipe: id } })
}
function setMode(m: Mode): void {
  void router.replace({ query: { ...route.query, mode: m === 'batch' ? 'batch' : undefined } })
}
function backToPicker(): void {
  void router.push({ query: { mode: route.query.mode, project: route.query.project } })
}

// ?project= wins (deep link from a project page); otherwise the last project picked here stays active.
const projectId = computed<string>(() => {
  const q = route.query.project
  if (typeof q === 'string' && q) return q
  return projects.activeId ?? NO_PROJECT
})
const project = computed(() => projects.byId(projectId.value))
// The list may have failed to load; the core still knows the project, so the choice is not dropped.
const runProjectId = computed(() => project.value?.id ?? (projectId.value !== NO_PROJECT ? projectId.value : undefined))
function setProject(id: string): void {
  projects.setActive(id)
  void router.replace({ query: { ...route.query, project: id === NO_PROJECT ? undefined : id } })
}
watch(
  () => conn.transport,
  (t) => t && !projects.loaded && !projects.loading && void projects.load().catch(() => undefined),
  { immediate: true }
)

const ORDER: RecipeGroup[] = ['2d', '3d', 'character', 'audio']
const groups = computed(() => {
  const sorted = [...recipes.list].sort((a, b) => a.name.localeCompare(b.name, 'vi'))
  return ORDER.map((g) => ({ id: g, label: RECIPE_GROUP_LABEL[g], items: sorted.filter((r) => recipeGroup(r.id) === g) })).filter(
    (g) => g.items.length
  )
})
const lastRecipe = computed(() => (lastId.value ? recipes.byId(lastId.value) : undefined))
const flow = (r: RecipeInfo): string => [...new Set(r.steps.map((s) => stepName(s)))].join(' · ')
const asksPick = (r: RecipeInfo): boolean => r.steps.some((s) => s.kind === 'gate.pick')
const lanes = (r: RecipeInfo) => [...new Set(r.steps.map((s) => s.lane).filter((l) => l && l !== 'none'))]

// Median, not mean: wall time includes how long a gate waited for the user, and one overnight wait would skew it.
const ETA_SAMPLE = 20
const eta = computed(() => {
  const byRecipe = new Map<string, number[]>()
  for (const r of runs.runList) {
    if (r.status !== 'succeeded' || !r.ended_at) continue
    let arr = byRecipe.get(r.recipe_id)
    if (!arr) byRecipe.set(r.recipe_id, (arr = []))
    if (arr.length < ETA_SAMPLE) arr.push(r.ended_at - r.created_at)
  }
  const out = new Map<string, { sec: number; n: number }>()
  for (const [id, arr] of byRecipe) {
    const s = arr.sort((a, b) => a - b)
    const mid = Math.floor(s.length / 2)
    out.set(id, { sec: s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2, n: s.length })
  }
  return out
})
function etaLabel(id: string): string | null {
  const sec = eta.value.get(id)?.sec
  if (sec === undefined) return null
  if (sec < 45) return 'Dưới 1 phút'
  const m = Math.max(1, Math.round(sec / 60))
  return m < 60 ? `~${m} phút` : `~${Math.floor(m / 60)} giờ${m % 60 ? ` ${m % 60} phút` : ''}`
}

type FieldValue = string | number | boolean
const values = reactive<Record<string, FieldValue>>({})
const blobs = reactive<Record<string, BlobRef[]>>({})
const errors = reactive<Record<string, string | null>>({})
const title = ref('')
const submitting = ref(false)
const formEl = ref<HTMLFormElement | null>(null)

const isBlob = (t: string): t is 'image' | 'images' | 'mesh' | 'file' | 'video' =>
  t === 'image' || t === 'images' || t === 'mesh' || t === 'file' || t === 'video'
const isNum = (t: string): boolean => t === 'int' || t === 'float'

const textInputs = computed(() => Object.entries(selected.value?.inputs ?? {}).filter(([, s]) => s.type === 'text').map(([n]) => n))
const fileInputs = computed(() =>
  Object.entries(selected.value?.inputs ?? {}).filter(([, s]) => s.type === 'image' || s.type === 'mesh').map(([n]) => n)
)
type VaryBy = 'prompts' | 'files'
const varyBy = ref<VaryBy>('prompts')
const varying = computed(() => (mode.value !== 'batch' ? null : ((varyBy.value === 'prompts' ? textInputs : fileInputs).value[0] ?? null)))
const batchLines = ref('')
const batchFiles = ref<BlobRef[]>([])
const batchStyle = ref<string | null>(null)
// Inputs the user changed by hand; untouched ones a project style covers are left out of the request so the core fills them.
const touched = reactive(new Set<string>())
const withBatchStyle = (text: string): string => (batchStyle.value ? `${text.replace(/[,\s]+$/, '')}, ${batchStyle.value}` : text)
const unit = computed(() => (textInputs.value.length ? 'prompt' : 'file'))

watch(
  selected,
  (r) => {
    if (!r) return
    lastId.value = r.id
    try {
      localStorage.setItem(LAST_KEY, r.id)
    } catch {
      /* storage unavailable */
    }
    for (const k of Object.keys(values)) delete values[k]
    for (const k of Object.keys(errors)) delete errors[k]
    touched.clear()
    for (const [name, spec] of Object.entries(r.inputs)) {
      if (isBlob(spec.type)) blobs[name] = blobs[name] ?? []
      else values[name] = defaultOf(spec)
    }
    varyBy.value = textInputs.value.length ? 'prompts' : 'files'
    batchFiles.value = []
    void nextTick(() => formEl.value?.querySelector('textarea')?.focus())
  },
  { immediate: true }
)

// "Dùng tiếp cho…" links here with `input.<name>=<hash>` (repeated for list inputs); those files show as already added.
watch(
  selected,
  async (r) => {
    if (!r) return
    const wanted = Object.entries(route.query).filter(([k]) => k.startsWith(PREFILL_PREFIX))
    if (!wanted.length) return
    void router.replace({ query: Object.fromEntries(Object.entries(route.query).filter(([k]) => !k.startsWith(PREFILL_PREFIX))) })
    for (const [k, v] of wanted) {
      const name = k.slice(PREFILL_PREFIX.length)
      const hashes = (Array.isArray(v) ? v : [v]).filter((h): h is string => !!h)
      const spec = r.inputs[name]
      // "Tạo thêm giống" also passes the source run's text/number inputs.
      if (spec && !isBlob(spec.type) && hashes.length) {
        values[name] = isNum(spec.type) ? Number(hashes[0]) : spec.type === 'bool' ? hashes[0] === 'true' : hashes[0]!
        touched.add(name)
        continue
      }
      if (!isBlob(spec?.type ?? '') || !hashes.length) continue
      try {
        blobs[name] = await Promise.all(hashes.map((h) => api().blobInfo(h)))
        touched.add(name)
        errors[name] = null
      } catch (e) {
        toasts.error(e, 'Không nạp được tệp từ kết quả trước')
      }
    }
  },
  { immediate: true }
)

const fills = computed<StyleFill[]>(() => (selected.value && project.value ? styleFills(selected.value.inputs, normalizeStyle(project.value.style)) : []))
const fillByKey = computed(() => new Map(fills.value.filter((f) => !f.wraps).map((f) => [f.key, f])))
const projectFill = (name: string): StyleFill | undefined => (touched.has(name) ? undefined : fillByKey.value.get(name))
const appliedFills = computed(() => fills.value.filter((f) => f.wraps || !touched.has(f.key)))
const overriddenFills = computed(() => fills.value.filter((f) => !f.wraps && touched.has(f.key)))
const presetName = (id: string): string | undefined => presets.list.find((p) => p.id === id)?.name
const fillText = computed(() => fillSummary(appliedFills.value, presetName))

// Mirrors core engine/style_bible.apply_bible: which inputs of the recipe the bible can reach.
const bible = ref<StyleBible | null>(null)
const useBible = ref(true)
watch(
  () => project.value?.id,
  async (id) => {
    bible.value = null
    useBible.value = true
    if (!id) return
    try {
      const view = await api().getStyleBible(id)
      if (project.value?.id === id) bible.value = view.bible
    } catch {
      bible.value = null
    }
  },
  { immediate: true }
)
const bibleTargets = computed<string[]>(() => {
  const b = bible.value
  const r = selected.value
  if (!b?.active || !r) return []
  const kinds = r.steps.map((st) => st.kind)
  const visual = kinds.some((k) => /^(comfy\.(t2i|edit|inpaint)|codex\.|claude\.brief|blender\.|op\.npc_portraits)/.test(k))
  if (!visual && kinds.some((k) => /audio|sfx|music|tts\.|soundscape/.test(k))) return []
  const out: string[] = []
  const text = (n: string): boolean => (r.inputs[n]?.type ?? '') === 'text'
  if (b.suffix && (text('style') || text('prompt') || text('idea'))) out.push('hậu tố prompt')
  if (b.negative && text('negative')) out.push('negative')
  const styleRef = Object.entries(r.inputs).some(
    ([n, sp]) => sp.type === 'image' && (['style_ref', 'style_image', 'style_reference'].includes(n) || /style/i.test(sp.label ?? ''))
  )
  if (b.refs.length && styleRef) out.push('ảnh style mẫu')
  return out
})
const bibleParams = (): Record<string, Record<string, unknown>> | undefined =>
  bibleTargets.value.length && !useBible.value ? { $meta: { use_style_bible: false } } : undefined

function defaultOf(spec: RecipeInputSpec): FieldValue {
  return (spec.default as FieldValue | undefined) ?? (spec.type === 'bool' ? false : isNum(spec.type) ? (spec.min ?? 0) : '')
}

// Untouched fields show what the project will put there; the value itself still comes from the core's style apply.
function seedFromProject(): void {
  const r = selected.value
  if (!r) return
  for (const [name, spec] of Object.entries(r.inputs)) {
    if (isBlob(spec.type) || touched.has(name)) continue
    const f = fillByKey.value.get(name)
    values[name] = f ? (isNum(spec.type) ? Number(f.value) : (f.value as FieldValue)) : defaultOf(spec)
  }
}
// Values equal to the recipe default were not real choices; under another project they must not block its style.
watch(projectId, () => {
  const r = selected.value
  if (!r) return
  for (const name of [...touched]) {
    const spec = r.inputs[name]
    if (spec && !isBlob(spec.type) && values[name] === defaultOf(spec)) touched.delete(name)
  }
  seedFromProject()
})
watch(fillByKey, () => seedFromProject(), { immediate: true })
const emptyFallsBack = (name: string): boolean => !!project.value && fillByKey.value.has(name) && touched.has(name) && values[name] === ''

function resetToProject(name: string): void {
  touched.delete(name)
  errors[name] = null
  seedFromProject()
}

function onFieldEdit(name: string, e: Event): void {
  const t = e.target as HTMLElement | null
  if (e.type === 'input' || t?.closest('[role="radio"]')) touched.add(name)
}

const fields = computed(() => Object.entries(selected.value?.inputs ?? {}).filter(([n]) => n !== varying.value))

const lines = computed(() => batchLines.value.split('\n').map((l) => l.trim()).filter(Boolean))
const batchItems = computed(() => {
  const v = varying.value
  if (!v) return []
  if (varyBy.value === 'prompts')
    return lines.value.map((l) => ({ inputs: { [v]: l }, title: l.length > 60 ? `${l.slice(0, 59)}…` : l }))
  return batchFiles.value.map((b) => ({ inputs: { [v]: b.hash }, title: (b.name ?? 'file').replace(/\.\w+$/, '') }))
})

function label(name: string, spec: RecipeInputSpec): string {
  return spec.label ?? name
}

function errorFor(name: string, spec: RecipeInputSpec): string | null {
  if (isBlob(spec.type)) return spec.required !== false && !(blobs[name]?.length ?? 0) && !fillByKey.value.has(name) ? 'Cần thêm file' : null
  if (isNum(spec.type)) {
    const v = Number(values[name])
    if (Number.isNaN(v)) return 'Nhập một số'
    if (spec.min !== undefined && v < spec.min) return `Nhỏ nhất là ${spec.min}`
    if (spec.max !== undefined && v > spec.max) return `Lớn nhất là ${spec.max}`
    if (spec.type === 'int' && !Number.isInteger(v)) return 'Nhập số nguyên'
    return null
  }
  if (spec.type === 'text' && spec.required !== false && spec.default === undefined && !fillByKey.value.has(name) && !String(values[name] ?? '').trim())
    return 'Không được để trống'
  return null
}

function validate(name: string, spec: RecipeInputSpec): boolean {
  errors[name] = errorFor(name, spec)
  return !errors[name]
}

// tileset-seamless: Wang mixes the first image of prompt line 1 with line 2 (or two variants of one line); edge mode
// only uses line 1. The recipe's `rules` enforce the same in core; checked here too so the button says why.
const wangRecipe = computed(() => !!selected.value?.inputs.mode?.options?.includes('wang'))
const promptLines = computed(() => String(values.prompt ?? '').split('\n').filter((l) => l.trim()).length)
const wangBlock = computed<string | null>(() => {
  if (!wangRecipe.value || values.mode !== 'wang' || Number(values.count ?? 1) >= 2) return null
  if (mode.value === 'batch' && varying.value === 'prompt') return 'Wang chạy nhiều lượt: mỗi lượt 1 dòng, cần số ảnh ≥ 2'
  return promptLines.value < 2 ? 'Wang cần 2 dòng vật liệu, hoặc số ảnh mỗi vật liệu ≥ 2' : null
})
const tilesetHint = computed<string | null>(() => {
  if (!wangRecipe.value || mode.value === 'batch') return null
  if (values.mode !== 'wang' && promptLines.value > 1) return 'Kiểu edge chỉ dùng dòng 1; các dòng sau bị bỏ, không sinh ảnh.'
  if (values.mode === 'wang' && promptLines.value > 2) return 'Wang chỉ dùng 2 dòng đầu; dòng 3 trở đi bị bỏ.'
  if (values.mode === 'wang' && Number(values.count ?? 1) > 1)
    return 'Wang lấy ảnh liền mạch tốt nhất của mỗi dòng, các ảnh còn lại chỉ để chọn.'
  return null
})

// Why the run button is disabled, said next to it instead of a silently dimmed button.
const blockReason = computed<string | null>(() => {
  if (!conn.transport) return 'Đang chờ kết nối tới core'
  if (projectId.value !== NO_PROJECT && !projects.loaded && !projects.error) return 'Đang tải dự án'
  const r = selected.value
  if (!r) return null
  if (mode.value === 'batch') {
    if (!varying.value) return 'Quy trình này không có đầu vào để chạy nhiều lượt'
    if (!batchItems.value.length) return varyBy.value === 'prompts' ? 'Nhập ít nhất 1 prompt' : 'Thêm ít nhất 1 file'
  }
  const missingFiles = fields.value.filter(([n, s]) => isBlob(s.type) && errorFor(n, s)).map(([n, s]) => label(n, s))
  if (missingFiles.length) return `Cần thêm: ${missingFiles.join(', ')}`
  const emptyText = fields.value.find(([n, s]) => s.type === 'text' && errorFor(n, s))
  if (emptyText) return `Nhập ${label(emptyText[0], emptyText[1]).toLowerCase()} để chạy`
  return wangBlock.value
})

function collectInputs(): Record<string, unknown> {
  const inputs: Record<string, unknown> = {}
  for (const [name, spec] of fields.value) {
    if (project.value && fillByKey.value.has(name) && (!touched.has(name) || emptyFallsBack(name))) continue
    if (isBlob(spec.type)) {
      const refs = blobs[name] ?? []
      if (refs.length) inputs[name] = spec.type === 'images' || spec.siblings ? refs.map((b) => b.hash) : refs[0]!.hash
    } else if (isNum(spec.type)) inputs[name] = Number(values[name])
    else inputs[name] = values[name]
  }
  return inputs
}

async function submit(): Promise<void> {
  const r = selected.value
  if (!r || submitting.value) return
  const ok = fields.value.map(([n, s]) => validate(n, s)).every(Boolean)
  if (!ok) {
    const first = Object.keys(errors).find((k) => errors[k])
    document.getElementById(`in-${first}`)?.focus()
    return
  }
  if (blockReason.value) return
  const inputs = collectInputs()
  submitting.value = true
  try {
    const title0 = autoTitle(r, inputs)
    const textKeys = Object.keys(inputs).filter((k) => r.inputs[k]?.type === 'text' && hasVi(inputs[k]))
    const items = batchItems.value
    const itemKey = varying.value
    const promptBatch = mode.value === 'batch' && !!itemKey && varyBy.value === 'prompts'
    const batchTexts = promptBatch ? items.map((it) => String(it.inputs[itemKey!])) : []
    const english = await toEnglish([...textKeys.map((k) => String(inputs[k])), ...batchTexts])
    textKeys.forEach((k, i) => (inputs[k] = english[i]))
    // The shared style is already English, so it goes on after translation rather than through it.
    const itemsEn = promptBatch
      ? items.map((it, i) => ({ ...it, inputs: { ...it.inputs, [itemKey!]: withBatchStyle(english[textKeys.length + i] ?? batchTexts[i]!) } }))
      : items
    if (mode.value === 'batch') {
      const res = await api().startBatch({
        recipe_id: r.id,
        items: itemsEn,
        inputs,
        title: title.value.trim() || `${r.name} × ${batchItems.value.length}`,
        project_id: runProjectId.value,
        params: bibleParams()
      })
      toasts.push(`Đã tạo ${res.run_ids.length} lượt chạy`, 'success', 2600)
      batchLines.value = ''
      batchFiles.value = []
      await router.push(`/batch/${res.batch_id}`)
    } else {
      const { run_id } = await api().startRun({ recipe_id: r.id, inputs, title: title.value.trim() || title0, project_id: runProjectId.value, params: bibleParams() })
      toasts.push('Đã bắt đầu chạy', 'success', 2500)
      await router.push(`/runs/${run_id}`)
    }
  } catch (e) {
    toasts.error(e, mode.value === 'batch' ? 'Không tạo được lô' : 'Không bắt đầu được')
  } finally {
    submitting.value = false
  }
}

// Core names versions after the run title (else the recipe name), so default it to the prompt.
function autoTitle(r: RecipeInfo, inputs: Record<string, unknown>): string | undefined {
  const name = Object.keys(r.inputs).find((k) => r.inputs[k]!.type === 'text' && String(inputs[k] ?? '').trim())
  const text = name ? String(inputs[name]).trim().replace(/\s+/g, ' ') : ''
  return text ? (text.length > 60 ? `${text.slice(0, 59)}…` : text) : undefined
}

function onKey(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && selected.value) {
    e.preventDefault()
    void submit()
  }
}

const QUICK_INPUTS = new Set(['prompt', 'idea'])
const quickSubjects = computed(() => (/^(texture|tileset)-/.test(selected.value?.id ?? '') ? QUICK_MATERIALS : undefined))

// Local image models lose Vietnamese genre words ("rương", "tu tiên"); Claude and Codex steps read Vietnamese fine,
// so recipes that route the text through them keep it as typed.
const VI = /[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]/i
const hasVi = (s: unknown): boolean => typeof s === 'string' && VI.test(s)
const translatable = computed(
  () => !!selected.value && !selected.value.steps.some((st) => /^(claude|codex)\.|^blender\.agent$/.test(st.kind))
)
const translating = ref<string | null>(null)

function translateError(e: unknown): string {
  const err = e as Partial<ApiError>
  if (err.code === 'claude_timeout' || err.status === 504) return 'Claude dịch quá lâu nên đã dừng, thử lại sau ít phút.'
  if (err.status === 502) return 'Claude chưa dịch được (dịch vụ lỗi hoặc mất mạng).'
  if (err.code === 'timeout' || err.code === 'network') return `Không dịch được: ${(e as Error).message}.`
  return `Không dịch được: ${(e as Error).message ?? 'lỗi không rõ'}.`
}

async function translateField(name: string): Promise<void> {
  const text = String(values[name] ?? '')
  if (!hasVi(text) || translating.value) return
  translating.value = name
  try {
    const res = await api().translate([text])
    const out = res.texts?.[0]
    if (typeof out !== 'string' || !out.trim()) throw new Error('Core không trả bản dịch')
    if (String(values[name] ?? '') === text) values[name] = out
  } catch (e) {
    toasts.push(translateError(e), 'error', 6000)
  } finally {
    translating.value = null
  }
}

async function toEnglish(texts: string[]): Promise<string[]> {
  if (!translatable.value || !texts.some(hasVi)) return texts
  translating.value = '*'
  try {
    const res = await api().translate(texts)
    const out = texts.map((t, i) => {
      const r = res.texts?.[i]
      return typeof r === 'string' && r.trim() ? r : t
    })
    const missing = texts.filter((t, i) => hasVi(t) && out[i] === t).length
    if (missing) toasts.push(`${missing} prompt chưa dịch được, giữ tiếng Việt`, 'info', 3200)
    else toasts.push('Đã dịch prompt sang tiếng Anh', 'success', 2200)
    return out
  } catch (e) {
    toasts.push(`${translateError(e)} Chạy bằng tiếng Việt.`, 'error', 6000)
    return texts
  } finally {
    translating.value = null
  }
}
const EXAMPLES = ['thanh kiếm pha lê xanh, low-poly', 'khiên gỗ bọc đồng', 'bình máu đỏ phát sáng']
</script>

<template>
  <div class="view" @keydown="onKey">
    <PageHeader title="Tạo mới" :subtitle="selected ? 'Điền đầu vào rồi bấm chạy' : 'Chọn một quy trình để bắt đầu'">
      <template v-if="selected" #leading>
        <button class="btn btn-plain btn-icon" aria-label="Chọn quy trình khác" title="Chọn quy trình khác" @click="backToPicker">
          <ArrowLeft />
        </button>
      </template>
    </PageHeader>

    <div class="scroll">
      <!-- Picker -->
      <div v-if="!selected" class="picker stack">
        <p v-if="!recipes.loaded && !recipes.error" class="caption">Đang tải quy trình…</p>
        <EmptyState
          v-else-if="!recipes.list.length"
          :icon="BookOpen"
          :title="recipes.error ? 'Không tải được quy trình' : 'Chưa có quy trình nào'"
          :text="recipes.error ?? 'Core chưa nạp quy trình nào. Tạo một quy trình mới để bắt đầu.'"
          action-label="Quy trình mới"
          action-to="/recipes/new"
        >
          <button v-if="recipes.error" class="btn" @click="recipes.load(true)"><RotateCw /> Thử lại</button>
        </EmptyState>
        <template v-else>
          <button v-if="lastRecipe && selectedId === null" class="resume card" @click="pick(lastRecipe.id)">
            <RecipeThumb :id="lastRecipe.id" :size="40" />
            <span class="resume-text">
              <span class="caption">Dùng lại gần đây</span>
              <span class="headline">{{ lastRecipe.name }}</span>
            </span>
            <ArrowRight :size="16" class="chev" />
          </button>
          <p v-if="selectedId && recipes.loaded" class="caption warn">Không tìm thấy quy trình này. Chọn một quy trình khác.</p>
          <section v-for="g in groups" :key="g.id" class="group" :aria-labelledby="`g-${g.id}`">
            <h2 :id="`g-${g.id}`" class="section-label">{{ g.label }}</h2>
            <div class="grid">
              <button v-for="r in g.items" :key="r.id" class="rcard card" :title="`${flow(r)}

${r.description}`" @click="pick(r.id)">
                <span class="rcard-art"><RecipeThumb :id="r.id" :size="76" /></span>
                <span class="rcard-text">
                  <span class="headline rcard-name">{{ r.name }}</span>
                  <span class="callout rcard-flow">{{ recipePitch(r.id) ?? flow(r) }}</span>
                  <span v-if="asksPick(r) || RECOMMENDED_RECIPES.has(r.id) || etaLabel(r.id)" class="caption rcard-note">
                    <span v-if="RECOMMENDED_RECIPES.has(r.id)" class="rec">Khuyên dùng</span>
                    <span
                      v-if="etaLabel(r.id)"
                      class="eta num"
                      :title="`Thời gian giữa của ${eta.get(r.id)?.n} lượt chạy xong gần nhất, gồm cả lúc chờ bạn duyệt`"
                    >
                      <Timer :size="12" aria-hidden="true" />{{ etaLabel(r.id) }}
                    </span>
                    <template v-if="asksPick(r)">Có bước bạn chọn ảnh</template>
                  </span>
                  <span class="rcard-lanes">
                    <LaneTag v-for="l in lanes(r)" :key="l" :lane="l" />
                  </span>
                </span>
              </button>
            </div>
          </section>
        </template>
      </div>

      <!-- Form -->
      <form v-else ref="formEl" :key="selected.id" class="form" novalidate @submit.prevent="submit">
        <header class="f-head">
          <RecipeThumb :id="selected.id" :size="64" class="f-art" />
          <div class="f-title-text">
            <h2 class="title-1">{{ selected.name }}</h2>
            <ol class="pipeline" aria-label="Các bước">
              <li v-for="(s, i) in selected.steps" :key="s.id">
                <span class="p-step" :title="s.kind">{{ stepName(s) }}</span>
                <ArrowRight v-if="i < selected.steps.length - 1" :size="12" class="p-arrow" aria-hidden="true" />
              </li>
            </ol>
          </div>
        </header>

        <div class="modebar">
          <div class="seg" role="radiogroup" aria-label="Số lượt chạy">
            <button type="button" role="radio" :aria-checked="mode === 'single'" :class="{ on: mode === 'single' }" @click="setMode('single')">
              Một {{ unit }}
            </button>
            <button type="button" role="radio" :aria-checked="mode === 'batch'" :class="{ on: mode === 'batch' }" @click="setMode('batch')">
              <Layers :size="13" /> Nhiều {{ unit }}
            </button>
          </div>
          <ProjectSelect class="mb-project" :model-value="projectId" label="Dự án cho lượt chạy" @update:model-value="setProject" />
          <div v-if="mode === 'batch' && textInputs.length && fileInputs.length" class="seg" role="radiogroup" aria-label="Thay đổi theo">
            <button type="button" role="radio" :aria-checked="varyBy === 'prompts'" :class="{ on: varyBy === 'prompts' }" @click="varyBy = 'prompts'">
              Mỗi dòng 1 lượt
            </button>
            <button type="button" role="radio" :aria-checked="varyBy === 'files'" :class="{ on: varyBy === 'files' }" @click="varyBy = 'files'">
              Mỗi file 1 lượt
            </button>
          </div>
        </div>

        <Transition name="chip">
          <div v-if="project" :key="project.id" class="proj-chip" role="status">
            <FolderKanban :size="15" class="pc-ic" aria-hidden="true" />
            <div class="pc-text">
              <p>
                Thuộc dự án <strong>{{ project.name }}</strong>
                <template v-if="appliedFills.length">{{ ' · ' }}Sẽ áp: {{ fillText }}</template>
                <template v-else-if="!fills.length">{{ ' · ' }}Phong cách dự án không có mục nào hợp với quy trình này</template>
              </p>
              <p v-if="overriddenFills.length" class="caption">
                Giữ giá trị bạn đã chỉnh: {{ [...new Set(overriddenFills.map((f) => f.label))].join(', ') }}
              </p>
              <label v-if="bibleTargets.length" class="pc-bible caption" :title="bible?.suffix">
                <input v-model="useBible" type="checkbox" /> Áp style dự án <span class="pc-bible-v">(bản {{ bible?.version }}: {{ bibleTargets.join(', ') }})</span>
              </label>
            </div>
            <RouterLink :to="`/projects/${project.id}`" class="pc-link">Sửa phong cách</RouterLink>
          </div>
        </Transition>

        <div class="fields card">
          <div v-if="mode === 'batch' && varying" class="f">
            <label :for="varyBy === 'prompts' ? 'in-batch' : undefined" class="f-label">
              {{ varyBy === 'prompts' ? `${label(varying, selected.inputs[varying]!)}, mỗi dòng một lượt` : 'File, mỗi file một lượt' }}
            </label>
            <div class="f-control">
              <QuickPrompts
                v-if="varyBy === 'prompts' && QUICK_INPUTS.has(varying)"
                v-model="batchLines"
                v-model:batch-style="batchStyle"
                :subjects="quickSubjects"
                :hide-styles="!!quickSubjects"
                batch
              />
              <textarea
                v-if="varyBy === 'prompts'"
                id="in-batch"
                v-model="batchLines"
                class="field"
                rows="6"
                :placeholder="`Ví dụ:\n${EXAMPLES.join('\n')}`"
              />
              <BlobDropZone
                v-else
                v-model="batchFiles"
                :kind="selected.inputs[varying]?.type === 'mesh' ? 'mesh' : 'images'"
                label="Thả nhiều file vào đây"
              />
              <p class="caption count">
                <Layers :size="13" />
                <span class="num"><TweenNumber :value="batchItems.length" :duration="200" /> lượt chạy</span>
                <span v-if="varyBy === 'prompts' && batchStyle" :title="batchStyle">
                  · mỗi prompt thêm phong cách {{ quickStyleLabel(batchStyle) }}
                </span>
              </p>
            </div>
          </div>

          <div v-for="[name, spec] in fields" :key="name" class="f">
            <label :for="`in-${name}`" class="f-label">
              {{ label(name, spec) }}
              <span v-if="spec.required && !fillByKey.has(name)" class="req" aria-hidden="true">*</span>
              <span v-if="projectFill(name)" class="from-proj" :title="`Giá trị lấy từ dự án ${project?.name ?? ''}`">Theo dự án</span>
              <button
                v-else-if="project && fillByKey.has(name)"
                type="button"
                class="to-proj"
                :title="`Bỏ giá trị bạn chỉnh, dùng lại giá trị của dự án ${project.name}`"
                @click="resetToProject(name)"
              >
                <RotateCcw :size="11" /> Dùng của dự án
              </button>
            </label>
            <div class="f-control" @input="onFieldEdit(name, $event)" @click="onFieldEdit(name, $event)">
              <QuickPrompts
                v-if="spec.type === 'text' && QUICK_INPUTS.has(name)"
                :model-value="String(values[name] ?? '')"
                :subjects="quickSubjects"
                :hide-styles="!!quickSubjects"
                :batch="!!spec.multiline && !!quickSubjects"
                @update:model-value="(v) => ((values[name] = v), validate(name, spec))"
              />
              <textarea
                v-if="spec.type === 'text'"
                :id="`in-${name}`"
                :value="String(values[name] ?? '')"
                class="field"
                rows="3"
                :aria-invalid="!!errors[name]"
                :placeholder="`Ví dụ: ${quickSubjects ? 'nền đá cuội phủ rêu' : EXAMPLES[0]}`"
                @input="values[name] = ($event.target as HTMLTextAreaElement).value"
                @blur="validate(name, spec)"
              />
              <p v-if="name === 'prompt' && tilesetHint" class="caption">{{ tilesetHint }}</p>
              <div v-if="spec.type === 'text' && translatable && hasVi(values[name])" class="vi-row">
                <span class="caption">Model ảnh hiểu tiếng Anh tốt hơn — sẽ tự dịch khi chạy.</span>
                <button type="button" class="btn btn-plain small" :disabled="!!translating" @click="translateField(name)">
                  <LoaderCircle v-if="translating === name" class="spin" :size="14" />
                  <Languages v-else :size="14" />
                  Dịch ngay để xem
                </button>
              </div>
              <PresetSelect v-else-if="spec.type === 'preset'" :id="`in-${name}`" v-model="values[name]" />
              <div v-else-if="isNum(spec.type) && spec.options?.length" class="seg" role="radiogroup" :aria-label="label(name, spec)">
                <button
                  v-for="o in spec.options"
                  :id="spec.options[0] === o ? `in-${name}` : undefined"
                  :key="String(o)"
                  type="button"
                  role="radio"
                  :aria-checked="values[name] === o"
                  :class="{ on: values[name] === o }"
                  class="num"
                  @click="values[name] = o"
                >
                  {{ o }}
                </button>
              </div>
              <input
                v-else-if="isNum(spec.type)"
                :id="`in-${name}`"
                v-model.number="values[name]"
                class="field num narrow"
                type="number"
                :min="spec.min"
                :max="spec.max"
                :step="spec.step ?? (spec.type === 'int' ? 1 : 0.1)"
                :aria-invalid="!!errors[name]"
                @blur="validate(name, spec)"
              />
              <label v-else-if="spec.type === 'bool'" class="switch">
                <input :id="`in-${name}`" v-model="values[name]" type="checkbox" role="switch" />
                <span class="track"><span class="knob" /></span>
              </label>
              <div v-else-if="spec.options?.length" class="seg" role="radiogroup" :aria-label="label(name, spec)">
                <button
                  v-for="o in spec.options"
                  :key="String(o)"
                  type="button"
                  role="radio"
                  :aria-checked="values[name] === o"
                  :class="{ on: values[name] === o }"
                  @click="values[name] = o"
                >
                  {{ o }}
                </button>
              </div>
              <BlobDropZone
                v-else-if="isBlob(spec.type)"
                :id="`in-${name}`"
                v-model="blobs[name]"
                :kind="spec.type as 'image' | 'images' | 'mesh' | 'file' | 'video'"
                :ext="spec.ext"
                :siblings="spec.siblings"
                :label="label(name, spec)"
                :invalid="!!errors[name]"
                @update:model-value="(errors[name] = null), touched.add(name)"
              />
              <input v-else-if="spec.type !== 'text'" :id="`in-${name}`" v-model="values[name]" class="field" type="text" />
              <p v-if="isBlob(spec.type) && projectFill(name) && !(blobs[name]?.length ?? 0)" class="caption help">
                Để trống: dùng ảnh của dự án {{ project?.name }}
              </p>
              <p v-if="errors[name]" class="err" role="alert">{{ errors[name] }}</p>
              <p v-else-if="emptyFallsBack(name)" class="caption help">Để trống: dùng giá trị của dự án {{ project?.name }}</p>
              <p v-else-if="spec.help" class="caption help">{{ spec.help }}</p>
            </div>
          </div>
          <div class="f">
            <label for="in-title" class="f-label">{{ mode === 'batch' ? 'Tên lô' : 'Tên lượt chạy' }}</label>
            <div class="f-control">
              <input
                id="in-title"
                v-model="title"
                class="field"
                type="text"
                :placeholder="mode === 'batch' ? `Để trống: ${selected.name} × ${batchItems.length}` : 'Để trống: tự đặt theo mô tả'"
              />
            </div>
          </div>
        </div>

        <div class="submit-row">
          <span v-if="blockReason" id="run-block" class="caption reason" role="status">{{ blockReason }}</span>
          <span v-else class="caption"><span class="kbd">Ctrl</span> + <span class="kbd">Enter</span> để chạy</span>
          <button
            type="submit"
            class="btn btn-primary btn-lg"
            :disabled="submitting || !!blockReason"
            :aria-describedby="blockReason ? 'run-block' : undefined"
          >
            <LoaderCircle v-if="submitting" class="spin" />
            <Play v-else />
            <template v-if="submitting">Đang gửi…</template>
            <template v-else-if="mode === 'batch' && batchItems.length">Tạo <span class="num">{{ batchItems.length }}</span> lượt chạy</template>
            <template v-else-if="mode === 'batch'">Tạo lượt chạy</template>
            <template v-else>Bắt đầu chạy</template>
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.picker {
  gap: var(--sp-8);
  animation: fade-up var(--t-slow) var(--ease-out);
}
.group {
  display: grid;
  gap: 10px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.rcard {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px 12px 10px;
  border: 0;
  text-align: left;
  color: inherit;
  cursor: pointer;
  transition:
    transform var(--t-fast) var(--ease-spring),
    box-shadow var(--t-base) var(--ease-out);
}
.rcard:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.rcard:active {
  transform: scale(0.985);
}
.rcard-art {
  flex: none;
  display: grid;
  place-items: center;
  width: 84px;
  height: 84px;
  border-radius: var(--r-md);
  background: radial-gradient(circle at 50% 45%, var(--accent-soft), transparent 72%), var(--bg-sunken);
}
.rcard-text {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.rcard-name {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.rcard-flow {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rcard-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 8px;
  color: var(--text-3);
}
.rec {
  flex: none;
  white-space: nowrap;
  padding: 0 6px;
  border-radius: 999px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
}
.eta {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
}
.rcard-lanes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.resume {
  display: flex;
  align-items: center;
  gap: 12px;
  justify-self: start;
  min-width: 300px;
  padding: 8px 14px 8px 10px;
  border: 0;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: box-shadow var(--t-base) var(--ease-out);
}
.resume:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.resume-text {
  flex: 1;
  display: grid;
}
.chev {
  color: var(--text-3);
}
.warn {
  color: var(--st-waiting);
}
.form {
  max-width: 720px;
  margin: 0 auto;
  display: grid;
  gap: 18px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.f-head {
  display: flex;
  align-items: center;
  gap: 16px;
}
.f-title-text {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.f-art {
  animation: art-pop var(--t-slow) var(--ease-spring);
}
@keyframes art-pop {
  from {
    opacity: 0;
    transform: scale(0.9);
  }
}
@media (prefers-reduced-motion: reduce) {
  .f-art,
  .form,
  .picker {
    animation: none;
  }
}
.pipeline {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.pipeline li {
  display: flex;
  align-items: center;
  gap: 4px;
}
.p-step {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-elevated);
  box-shadow: var(--hairline);
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.p-arrow {
  color: var(--text-3);
}
.modebar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.fields {
  display: grid;
  padding: 4px 18px;
  container-type: inline-size;
}
.f {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 16px;
  padding: 14px 0;
  align-items: start;
}
.f + .f {
  border-top: 0.5px solid var(--separator);
}
.f-label {
  padding-top: 7px;
  font-weight: 500;
  color: var(--text-2);
}
.req {
  color: var(--st-fail);
}
@container (max-width: 460px) {
  .f {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .f-label {
    padding-top: 0;
  }
}
.f-control {
  min-width: 0;
}
.narrow {
  max-width: 160px;
}
.help {
  margin-top: 6px;
}
.count {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}
.err {
  margin-top: 6px;
  font-size: var(--fs-callout);
  color: var(--st-fail);
}
.field[aria-invalid='true'] {
  box-shadow: 0 0 0 1.5px var(--st-fail);
}
.switch {
  display: inline-flex;
  cursor: pointer;
  padding-top: 4px;
}
.switch input {
  position: absolute;
  opacity: 0;
  width: 1px;
  height: 1px;
}
.track {
  position: relative;
  width: 38px;
  height: 22px;
  border-radius: 11px;
  background: var(--bg-active);
  box-shadow: var(--hairline);
  transition: background-color var(--t-base) var(--ease-out);
}
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  transition: transform var(--t-base) var(--ease-spring);
}
.switch input:checked + .track {
  background: var(--accent);
}
.switch input:checked + .track .knob {
  transform: translateX(16px);
}
.switch input:focus-visible + .track {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.submit-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 14px;
  padding-bottom: 24px;
}
.reason {
  color: var(--text-2);
}
.spin {
  animation: spin 1s linear infinite;
}
.mb-project {
  margin-left: auto;
}
.proj-chip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--r-md);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 0.5px color-mix(in srgb, var(--accent) 35%, transparent);
  font-size: var(--fs-callout);
}
.pc-ic {
  flex: none;
  margin-top: 1px;
  color: var(--accent);
}
.pc-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.pc-bible {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  cursor: pointer;
}
.pc-bible-v {
  color: var(--text-3);
}
.pc-link {
  flex: none;
  color: var(--accent);
  font-weight: 500;
  text-decoration: none;
}
.pc-link:hover {
  text-decoration: underline;
}
.chip-enter-active,
.chip-leave-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.chip-enter-from,
.chip-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
.from-proj,
.to-proj {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 600;
  vertical-align: 1px;
}
.from-proj {
  color: var(--accent);
  background: var(--accent-soft);
}
.to-proj {
  border: 0;
  color: var(--text-2);
  background: var(--bg-active);
  cursor: pointer;
}
.to-proj:hover {
  color: var(--accent);
}
@media (prefers-reduced-motion: reduce) {
  .chip-enter-active,
  .chip-leave-active {
    transition: none;
  }
}
.vi-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px 8px;
  margin-top: 6px;
  color: var(--text-3);
}
</style>
