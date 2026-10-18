<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, BookOpen, LoaderCircle, Play } from '@lucide/vue'
import type { BlobRef, RecipeInfo, RecipeInputSpec } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import LaneTag from '@/components/LaneTag.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import { useRecipes } from '@/stores/recipes'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { kindName } from '@/composables/format'

const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

const LAST_KEY = 'studio.newrun.recipe'
function readLast(): string | null {
  try {
    return localStorage.getItem(LAST_KEY)
  } catch {
    return null
  }
}

const selectedId = ref<string | null>(readLast())
const selected = computed<RecipeInfo | undefined>(
  () => recipes.list.find((r) => r.id === selectedId.value) ?? recipes.list[0]
)

watch(
  () => conn.transport,
  (t) => t && void recipes.load(),
  { immediate: true }
)

type FieldValue = string | number | boolean
const values = reactive<Record<string, FieldValue>>({})
const blobs = reactive<Record<string, BlobRef[]>>({})
const errors = reactive<Record<string, string | null>>({})
const title = ref('')
const submitting = ref(false)
const promptEl = ref<HTMLTextAreaElement[] | null>(null)

const isBlob = (t: string): t is 'image' | 'images' | 'mesh' => t === 'image' || t === 'images' || t === 'mesh'
const isNum = (t: string): boolean => t === 'int' || t === 'float'

watch(
  selected,
  (r) => {
    if (!r) return
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
    void nextTick(() => promptEl.value?.[0]?.focus())
  },
  { immediate: true }
)

function label(name: string, spec: RecipeInputSpec): string {
  return spec.label ?? name
}

function validate(name: string, spec: RecipeInputSpec): boolean {
  let err: string | null = null
  if (isBlob(spec.type)) {
    if (spec.required !== false && !(blobs[name]?.length ?? 0)) err = 'Cần thêm file'
  } else if (isNum(spec.type)) {
    const v = Number(values[name])
    if (Number.isNaN(v)) err = 'Nhập một số'
    else if (spec.min !== undefined && v < spec.min) err = `Nhỏ nhất là ${spec.min}`
    else if (spec.max !== undefined && v > spec.max) err = `Lớn nhất là ${spec.max}`
    else if (spec.type === 'int' && !Number.isInteger(v)) err = 'Nhập số nguyên'
  } else if (spec.type === 'text' && spec.required !== false && spec.default === undefined && !String(values[name] ?? '').trim()) {
    err = 'Không được để trống'
  }
  errors[name] = err
  return !err
}

async function submit(): Promise<void> {
  const r = selected.value
  if (!r || submitting.value) return
  const ok = Object.entries(r.inputs).map(([n, s]) => validate(n, s)).every(Boolean)
  if (!ok) {
    const first = Object.keys(errors).find((k) => errors[k])
    document.getElementById(`in-${first}`)?.focus()
    return
  }
  const inputs: Record<string, unknown> = {}
  for (const [name, spec] of Object.entries(r.inputs)) {
    if (isBlob(spec.type)) inputs[name] = spec.type === 'images' ? blobs[name]!.map((b) => b.hash) : blobs[name]![0]?.hash
    else if (isNum(spec.type)) inputs[name] = Number(values[name])
    else inputs[name] = values[name]
  }
  submitting.value = true
  try {
    const { run_id } = await api().startRun({ recipe_id: r.id, inputs, title: title.value.trim() || autoTitle(r, inputs) })
    toasts.push('Đã bắt đầu chạy', 'success', 2500)
    await router.push(`/runs/${run_id}`)
  } catch (e) {
    toasts.error(e, 'Không bắt đầu được')
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
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    void submit()
  }
}
</script>

<template>
  <div class="view" @keydown="onKey">
    <PageHeader title="Tạo mới" subtitle="Chọn recipe, điền đầu vào, bấm chạy" />
    <div class="split">
      <aside class="recipes" aria-label="Recipe">
        <p class="section-label pad">Recipe</p>
        <div v-if="!recipes.loaded" class="pad caption">Đang tải…</div>
        <p v-else-if="recipes.error" class="pad err">{{ recipes.error }}</p>
        <button
          v-for="r in recipes.list"
          :key="r.id"
          class="recipe"
          :class="{ on: selected?.id === r.id }"
          :aria-pressed="selected?.id === r.id"
          @click="selectedId = r.id"
        >
          <span class="r-name">{{ r.name }}</span>
          <span class="caption r-desc">{{ r.description }}</span>
        </button>
      </aside>

      <section class="form-pane scroll">
        <EmptyState v-if="recipes.loaded && !selected" :icon="BookOpen" title="Chưa có recipe" text="Core chưa nạp recipe nào. Thêm file YAML vào core/studio_core/recipes rồi khởi động lại." />
        <form v-else-if="selected" class="form" novalidate @submit.prevent="submit">
          <header class="f-head">
            <h2 class="large-title">{{ selected.name }}</h2>
            <p class="secondary">{{ selected.description }}</p>
            <ol class="pipeline" aria-label="Các bước">
              <li v-for="(s, i) in selected.steps" :key="s.id">
                <span class="p-step">
                  <span class="p-name">{{ kindName(s.kind) }}</span>
                  <LaneTag :lane="s.lane" />
                </span>
                <ArrowRight v-if="i < selected.steps.length - 1" :size="14" class="p-arrow" />
              </li>
            </ol>
          </header>

          <div class="fields card">
            <div v-for="(spec, name) in selected.inputs" :key="name" class="f">
              <label :for="`in-${name}`" class="f-label">
                {{ label(String(name), spec) }}
                <span v-if="spec.required" class="req" aria-hidden="true">*</span>
              </label>
              <div class="f-control">
                <textarea
                  v-if="spec.type === 'text'"
                  :id="`in-${name}`"
                  ref="promptEl"
                  v-model="values[name]"
                  class="field"
                  rows="3"
                  :aria-invalid="!!errors[name]"
                  placeholder="Ví dụ: thanh kiếm pha lê xanh, low-poly"
                  @blur="validate(String(name), spec)"
                />
                <div v-else-if="isNum(spec.type) && spec.options?.length" class="segmented" role="radiogroup" :aria-label="label(String(name), spec)">
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
                  @blur="validate(String(name), spec)"
                />
                <label v-else-if="spec.type === 'bool'" class="switch">
                  <input :id="`in-${name}`" v-model="values[name]" type="checkbox" role="switch" />
                  <span class="track"><span class="knob" /></span>
                </label>
                <div v-else-if="spec.options?.length" class="segmented" role="radiogroup">
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
                  :label="label(String(name), spec)"
                  :invalid="!!errors[name]"
                  @update:model-value="errors[name] = null"
                />
                <input v-else :id="`in-${name}`" v-model="values[name]" class="field" type="text" />
                <p v-if="errors[name]" class="err" role="alert">{{ errors[name] }}</p>
                <p v-else-if="spec.help" class="caption help">{{ spec.help }}</p>
              </div>
            </div>
            <div class="f">
              <label for="in-title" class="f-label">Tên run</label>
              <div class="f-control">
                <input id="in-title" v-model="title" class="field" type="text" placeholder="Tự đặt theo prompt nếu để trống" />
              </div>
            </div>
          </div>

          <div class="submit-row">
            <span class="caption">Ctrl + Enter để chạy</span>
            <button type="submit" class="btn btn-primary btn-lg" :disabled="submitting || !conn.transport">
              <LoaderCircle v-if="submitting" class="spin" />
              <Play v-else />
              {{ submitting ? 'Đang gửi…' : 'Bắt đầu chạy' }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>

<style scoped>
.split {
  flex: 1;
  min-height: 0;
  display: flex;
}
.recipes {
  width: 260px;
  flex: none;
  overflow: auto;
  padding: 16px 10px;
  border-right: 0.5px solid var(--separator);
  display: grid;
  align-content: start;
  gap: 4px;
}
.pad {
  padding: 0 8px 6px;
}
.recipe {
  display: grid;
  gap: 2px;
  text-align: left;
  padding: 10px 12px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    transform var(--t-fast) var(--ease-spring);
}
.recipe:hover {
  background: var(--bg-hover);
}
.recipe:active {
  transform: scale(0.985);
}
.recipe.on {
  background: var(--bg-selected);
}
.r-name {
  font-weight: 600;
}
.r-desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.form-pane {
  flex: 1;
}
.form {
  max-width: 720px;
  margin: 0 auto;
  display: grid;
  gap: 20px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.f-head {
  display: grid;
  gap: 6px;
}
.pipeline {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.pipeline li {
  display: flex;
  align-items: center;
  gap: 6px;
}
.p-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  border-radius: 999px;
  background: var(--bg-elevated);
  box-shadow: var(--hairline);
  font-size: var(--fs-callout);
}
.p-arrow {
  color: var(--text-3);
}
.fields {
  display: grid;
  padding: 4px 18px;
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
.f-control {
  min-width: 0;
}
.narrow {
  max-width: 160px;
}
.help {
  margin-top: 6px;
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
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.segmented button {
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
.spin {
  animation: spin 1s linear infinite;
}
</style>
