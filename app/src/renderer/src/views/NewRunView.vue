<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, BookOpen, Languages, Layers, LoaderCircle, Play } from '@lucide/vue'
import type { BlobRef, RecipeInfo, RecipeInputSpec } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import LaneTag from '@/components/LaneTag.vue'
import QuickPrompts from '@/components/QuickPrompts.vue'
import EmptyState from '@/components/EmptyState.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import PresetSelect from '@/components/PresetSelect.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useRecipes } from '@/stores/recipes'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { stepName } from '@/composables/format'
import { RECIPE_GROUP_LABEL, RECOMMENDED_RECIPES, recipeGroup, recipePitch, type RecipeGroup } from '@/assets/art'

const recipes = useRecipes()
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
  void router.push({ query: { mode: route.query.mode } })
}

const ORDER: RecipeGroup[] = ['2d', '3d', 'character']
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

type FieldValue = string | number | boolean
const values = reactive<Record<string, FieldValue>>({})
const blobs = reactive<Record<string, BlobRef[]>>({})
const errors = reactive<Record<string, string | null>>({})
const title = ref('')
const submitting = ref(false)
const formEl = ref<HTMLFormElement | null>(null)

const isBlob = (t: string): t is 'image' | 'images' | 'mesh' => t === 'image' || t === 'images' || t === 'mesh'
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
    for (const [name, spec] of Object.entries(r.inputs)) {
      if (isBlob(spec.type)) blobs[name] = blobs[name] ?? []
      else values[name] = (spec.default as FieldValue | undefined) ?? (spec.type === 'bool' ? false : isNum(spec.type) ? (spec.min ?? 0) : '')
    }
    varyBy.value = textInputs.value.length ? 'prompts' : 'files'
    batchFiles.value = []
    void nextTick(() => formEl.value?.querySelector('textarea')?.focus())
  },
  { immediate: true }
)

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
  if (isBlob(spec.type)) return spec.required !== false && !(blobs[name]?.length ?? 0) ? 'Cần thêm file' : null
  if (isNum(spec.type)) {
    const v = Number(values[name])
    if (Number.isNaN(v)) return 'Nhập một số'
    if (spec.min !== undefined && v < spec.min) return `Nhỏ nhất là ${spec.min}`
    if (spec.max !== undefined && v > spec.max) return `Lớn nhất là ${spec.max}`
    if (spec.type === 'int' && !Number.isInteger(v)) return 'Nhập số nguyên'
    return null
  }
  if (spec.type === 'text' && spec.required !== false && spec.default === undefined && !String(values[name] ?? '').trim())
    return 'Không được để trống'
  return null
}

function validate(name: string, spec: RecipeInputSpec): boolean {
  errors[name] = errorFor(name, spec)
  return !errors[name]
}

// Why the run button is disabled, said next to it instead of a silently dimmed button.
const blockReason = computed<string | null>(() => {
  if (!conn.transport) return 'Đang chờ kết nối tới core'
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
  return null
})

function collectInputs(): Record<string, unknown> {
  const inputs: Record<string, unknown> = {}
  for (const [name, spec] of fields.value) {
    if (isBlob(spec.type)) {
      const refs = blobs[name] ?? []
      if (refs.length) inputs[name] = spec.type === 'images' ? refs.map((b) => b.hash) : refs[0]!.hash
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
    const batchTexts = mode.value === 'batch' && itemKey && varyBy.value === 'prompts' ? items.map((it) => String(it.inputs[itemKey])) : []
    const english = await toEnglish([...textKeys.map((k) => String(inputs[k])), ...batchTexts])
    textKeys.forEach((k, i) => (inputs[k] = english[i]))
    const itemsEn = batchTexts.length
      ? items.map((it, i) => ({ ...it, inputs: { ...it.inputs, [itemKey!]: english[textKeys.length + i] } }))
      : items
    if (mode.value === 'batch') {
      const res = await api().startBatch({
        recipe_id: r.id,
        items: itemsEn,
        inputs,
        title: title.value.trim() || `${r.name} × ${batchItems.value.length}`
      })
      toasts.push(`Đã tạo ${res.run_ids.length} lượt chạy`, 'success', 2600)
      batchLines.value = ''
      batchFiles.value = []
      await router.push(`/batch/${res.batch_id}`)
    } else {
      const { run_id } = await api().startRun({ recipe_id: r.id, inputs, title: title.value.trim() || title0 })
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

// Local image models lose Vietnamese genre words ("rương", "tu tiên"); Claude and Codex steps read Vietnamese fine,
// so recipes that route the text through them keep it as typed.
const VI = /[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]/i
const hasVi = (s: unknown): boolean => typeof s === 'string' && VI.test(s)
const translatable = computed(
  () => !!selected.value && !selected.value.steps.some((st) => /^(claude|codex)\.|^blender\.agent$/.test(st.kind))
)
const translating = ref<string | null>(null)

async function translateField(name: string): Promise<void> {
  const text = String(values[name] ?? '')
  if (!hasVi(text) || translating.value) return
  translating.value = name
  try {
    const res = await api().translate([text])
    if (String(values[name] ?? '') === text) values[name] = res.texts[0] ?? text
  } catch (e) {
    toasts.error(e, 'Không dịch được')
  } finally {
    translating.value = null
  }
}

async function toEnglish(texts: string[]): Promise<string[]> {
  if (!translatable.value || !texts.some(hasVi)) return texts
  translating.value = '*'
  try {
    const res = await api().translate(texts)
    toasts.push('Đã dịch prompt sang tiếng Anh', 'success', 2200)
    return res.texts
  } catch (e) {
    toasts.error(e, 'Không dịch được, chạy bằng tiếng Việt')
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
        <p v-if="!recipes.loaded" class="caption">Đang tải quy trình…</p>
        <EmptyState
          v-else-if="!recipes.list.length"
          :icon="BookOpen"
          title="Chưa có quy trình nào"
          :text="recipes.error ?? 'Core chưa nạp quy trình nào. Tạo một quy trình mới để bắt đầu.'"
          action-label="Quy trình mới"
          action-to="/recipes/new"
        />
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
                  <span v-if="asksPick(r) || RECOMMENDED_RECIPES.has(r.id)" class="caption rcard-note">
                    <span v-if="RECOMMENDED_RECIPES.has(r.id)" class="rec">Khuyên dùng</span>
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
          <div class="segmented" role="radiogroup" aria-label="Số lượt chạy">
            <button type="button" role="radio" :aria-checked="mode === 'single'" :class="{ on: mode === 'single' }" @click="setMode('single')">
              Một {{ unit }}
            </button>
            <button type="button" role="radio" :aria-checked="mode === 'batch'" :class="{ on: mode === 'batch' }" @click="setMode('batch')">
              <Layers :size="13" /> Nhiều {{ unit }}
            </button>
          </div>
          <div v-if="mode === 'batch' && textInputs.length && fileInputs.length" class="segmented small" role="radiogroup" aria-label="Thay đổi theo">
            <button type="button" role="radio" :aria-checked="varyBy === 'prompts'" :class="{ on: varyBy === 'prompts' }" @click="varyBy = 'prompts'">
              Mỗi dòng 1 lượt
            </button>
            <button type="button" role="radio" :aria-checked="varyBy === 'files'" :class="{ on: varyBy === 'files' }" @click="varyBy = 'files'">
              Mỗi file 1 lượt
            </button>
          </div>
        </div>

        <div class="fields card">
          <div v-if="mode === 'batch' && varying" class="f">
            <label :for="varyBy === 'prompts' ? 'in-batch' : undefined" class="f-label">
              {{ varyBy === 'prompts' ? `${label(varying, selected.inputs[varying]!)}, mỗi dòng một lượt` : 'File, mỗi file một lượt' }}
            </label>
            <div class="f-control">
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
              </p>
            </div>
          </div>

          <div v-for="[name, spec] in fields" :key="name" class="f">
            <label :for="`in-${name}`" class="f-label">
              {{ label(name, spec) }}
              <span v-if="spec.required" class="req" aria-hidden="true">*</span>
            </label>
            <div class="f-control">
              <QuickPrompts
                v-if="spec.type === 'text' && QUICK_INPUTS.has(name)"
                :model-value="String(values[name] ?? '')"
                @update:model-value="(v) => ((values[name] = v), validate(name, spec))"
              />
              <textarea
                v-if="spec.type === 'text'"
                :id="`in-${name}`"
                :value="String(values[name] ?? '')"
                class="field"
                rows="3"
                :aria-invalid="!!errors[name]"
                :placeholder="`Ví dụ: ${EXAMPLES[0]}`"
                @input="values[name] = ($event.target as HTMLTextAreaElement).value"
                @blur="validate(name, spec)"
              />
              <div v-if="spec.type === 'text' && translatable && hasVi(values[name])" class="vi-row">
                <span class="caption">Model ảnh hiểu tiếng Anh tốt hơn — sẽ tự dịch khi chạy.</span>
                <button type="button" class="btn btn-plain small" :disabled="!!translating" @click="translateField(name)">
                  <LoaderCircle v-if="translating === name" class="spin" :size="14" />
                  <Languages v-else :size="14" />
                  Dịch ngay để xem
                </button>
              </div>
              <PresetSelect v-else-if="spec.type === 'preset'" :id="`in-${name}`" v-model="values[name]" />
              <div v-else-if="isNum(spec.type) && spec.options?.length" class="segmented" role="radiogroup" :aria-label="label(name, spec)">
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
              <div v-else-if="spec.options?.length" class="segmented" role="radiogroup" :aria-label="label(name, spec)">
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
                :kind="spec.type as 'image' | 'images' | 'mesh'"
                :label="label(name, spec)"
                :invalid="!!errors[name]"
                @update:model-value="errors[name] = null"
              />
              <input v-else :id="`in-${name}`" v-model="values[name]" class="field" type="text" />
              <p v-if="errors[name]" class="err" role="alert">{{ errors[name] }}</p>
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
  align-items: center;
  gap: 6px;
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
.segmented {
  display: inline-flex;
  flex-wrap: wrap;
  max-width: 100%;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.segmented button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 52px;
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    box-shadow var(--t-fast);
}
.segmented.small button {
  height: 24px;
  font-size: var(--fs-callout);
}
.segmented button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-weight: 600;
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
  background: var(--st-ok);
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
