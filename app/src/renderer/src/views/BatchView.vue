<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, Layers, ListPlus, LoaderCircle, Play } from '@lucide/vue'
import type { BatchItem, BlobRef, RecipeInfo, RecipeInputSpec } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import RelTime from '@/components/RelTime.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import ParamField from '@/components/flow/ParamField.vue'
import { useRecipes } from '@/stores/recipes'
import { useBatches } from '@/stores/batches'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const recipes = useRecipes()
const batches = useBatches()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

watch(
  () => conn.transport,
  (t) => t && void recipes.load(),
  { immediate: true }
)

const recipeId = ref<string>('')
const recipe = computed<RecipeInfo | undefined>(() => recipes.byId(recipeId.value) ?? recipes.list[0])
const isBlob = (s: RecipeInputSpec): boolean => s.type === 'image' || s.type === 'mesh'

type Mode = 'prompts' | 'files'
const mode = ref<Mode>('prompts')
const textInputs = computed(() => Object.entries(recipe.value?.inputs ?? {}).filter(([, s]) => s.type === 'text').map(([n]) => n))
const blobInputs = computed(() => Object.entries(recipe.value?.inputs ?? {}).filter(([, s]) => isBlob(s)).map(([n]) => n))
const varying = ref('')
const prompts = ref('')
const files = ref<BlobRef[]>([])
const common = reactive<Record<string, unknown>>({})
const commonBlobs = reactive<Record<string, BlobRef[]>>({})
const title = ref('')
const submitting = ref(false)

watch(
  recipe,
  (r) => {
    if (!r) return
    recipeId.value = r.id
    for (const k of Object.keys(common)) delete common[k]
    for (const k of Object.keys(commonBlobs)) delete commonBlobs[k]
    if (!textInputs.value.length && blobInputs.value.length) mode.value = 'files'
    if (!blobInputs.value.length) mode.value = 'prompts'
    varying.value = (mode.value === 'prompts' ? textInputs.value[0] : blobInputs.value[0]) ?? ''
    files.value = []
  },
  { immediate: true }
)
watch(mode, (m) => (varying.value = (m === 'prompts' ? textInputs.value[0] : blobInputs.value[0]) ?? ''))

const lines = computed(() => prompts.value.split('\n').map((l) => l.trim()).filter(Boolean))
const items = computed<BatchItem[]>(() => {
  if (!varying.value) return []
  if (mode.value === 'prompts')
    return lines.value.map((l) => ({ inputs: { [varying.value]: l }, title: l.length > 60 ? `${l.slice(0, 59)}…` : l }))
  return files.value.map((b) => ({ inputs: { [varying.value]: b.hash }, title: (b.name ?? b.hash.slice(0, 8)).replace(/\.\w+$/, '') }))
})
const otherInputs = computed(() => Object.entries(recipe.value?.inputs ?? {}).filter(([n]) => n !== varying.value))
const missing = computed(() =>
  otherInputs.value.filter(([n, s]) => isBlob(s) && s.required !== false && !(commonBlobs[n]?.length ?? 0)).map(([n, s]) => s.label ?? n)
)

async function submit(): Promise<void> {
  const r = recipe.value
  if (!r || !items.value.length || submitting.value) return
  const inputs: Record<string, unknown> = {}
  for (const [n, v] of Object.entries(common)) if (v !== undefined) inputs[n] = v
  for (const [n, refs] of Object.entries(commonBlobs)) if (refs.length) inputs[n] = refs[0]!.hash
  submitting.value = true
  try {
    const res = await api().startBatch({ recipe_id: r.id, items: items.value, inputs, title: title.value.trim() || `${r.name} × ${items.value.length}` })
    toasts.push(`Đã tạo ${res.run_ids.length} lượt chạy`, 'success', 2600)
    prompts.value = ''
    files.value = []
    await router.push(`/batch/${res.batch_id}`)
  } catch (e) {
    toasts.error(e, 'Không tạo được lô')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Chạy hàng loạt" subtitle="Một recipe, nhiều prompt hoặc nhiều file — GPU xử lý theo từng công đoạn" />
    <div class="scroll">
      <div class="stack">
        <section class="card form">
          <div class="row">
            <label for="b-recipe" class="lbl">Recipe</label>
            <div class="ctl recipe-pick">
              <RecipeThumb v-if="recipe" :id="recipe.id" :size="36" />
              <select id="b-recipe" v-model="recipeId" class="field">
                <option v-for="r in recipes.list" :key="r.id" :value="r.id">{{ r.name }}</option>
              </select>
            </div>
          </div>

          <div class="row">
            <span class="lbl">Thay đổi theo</span>
            <div class="ctl">
              <div class="seg" role="radiogroup" aria-label="Kiểu lô">
                <button type="button" role="radio" :aria-checked="mode === 'prompts'" :class="{ on: mode === 'prompts' }" :disabled="!textInputs.length" @click="mode = 'prompts'">Danh sách prompt</button>
                <button type="button" role="radio" :aria-checked="mode === 'files'" :class="{ on: mode === 'files' }" :disabled="!blobInputs.length" @click="mode = 'files'">Nhiều file</button>
              </div>
              <select v-if="(mode === 'prompts' ? textInputs : blobInputs).length > 1" v-model="varying" class="field narrow" aria-label="Đầu vào thay đổi">
                <option v-for="n in mode === 'prompts' ? textInputs : blobInputs" :key="n" :value="n">{{ recipe?.inputs[n]?.label ?? n }}</option>
              </select>
            </div>
          </div>

          <div class="row">
            <label :for="mode === 'prompts' ? 'b-lines' : undefined" class="lbl">
              {{ mode === 'prompts' ? 'Prompt, mỗi dòng một lượt' : 'File, mỗi file một lượt' }}
            </label>
            <div class="ctl col">
              <textarea
                v-if="mode === 'prompts'"
                id="b-lines"
                v-model="prompts"
                class="field mono-ish"
                rows="7"
                placeholder="thanh kiếm pha lê xanh&#10;khiên gỗ bọc đồng&#10;bình máu đỏ phát sáng"
              />
              <BlobDropZone v-else v-model="files" :kind="recipe?.inputs[varying]?.type === 'mesh' ? 'mesh' : 'images'" label="Thả nhiều file vào đây" />
              <p class="caption count">
                <Layers :size="13" />
                <span><TweenNumber :value="items.length" :duration="200" /> lượt chạy</span>
              </p>
            </div>
          </div>

          <div v-for="[name, spec] in otherInputs" :key="name" class="row">
            <span class="lbl">{{ spec.label ?? name }}</span>
            <div class="ctl col">
              <BlobDropZone v-if="isBlob(spec)" v-model="commonBlobs[name]" :kind="spec.type === 'mesh' ? 'mesh' : 'image'" :label="spec.label ?? name" />
              <ParamField v-else :name="name" :spec="spec" :value="common[name]" :inputs="[]" bare @change="common[name] = $event" />
            </div>
          </div>

          <div class="row">
            <label for="b-title" class="lbl">Tên lô</label>
            <div class="ctl"><input id="b-title" v-model="title" class="field" :placeholder="recipe ? `${recipe.name} × ${items.length}` : ''" /></div>
          </div>

          <div class="submit">
            <span v-if="missing.length" class="caption warn">Cần thêm: {{ missing.join(', ') }}</span>
            <button class="btn btn-primary btn-lg" :disabled="!items.length || submitting || missing.length > 0" @click="submit">
              <LoaderCircle v-if="submitting" class="spin" />
              <Play v-else />
              Tạo {{ items.length || '' }} lượt chạy
            </button>
          </div>
        </section>

        <section class="list">
          <h2 class="section-label">Các lô gần đây</h2>
          <EmptyState v-if="!batches.list.length" :icon="ListPlus" title="Chưa có lô nào" text="Dán danh sách prompt ở trên để chạy nhiều asset một lần." />
          <div v-else v-auto-animate class="batches">
            <RouterLink v-for="b in batches.list" :key="b.id" :to="`/batch/${b.id}`" class="batch card">
              <RecipeThumb :id="b.recipeId" :size="40" />
              <div class="b-text">
                <span class="b-title">{{ b.title || recipes.byId(b.recipeId)?.name || b.recipeId }}</span>
                <span class="caption"><RelTime :ts="b.createdAt" /> · {{ b.runs.length }} lượt</span>
                <div class="bar" :aria-label="`${b.done}/${b.runs.length} xong`">
                  <span class="seg-ok" :style="{ transform: `scaleX(${b.done / b.runs.length})` }" />
                  <span class="seg-bad" :style="{ transform: `scaleX(${b.failed / b.runs.length})`, left: `${(b.done / b.runs.length) * 100}%` }" />
                </div>
              </div>
              <div class="b-stats num">
                <span class="ok"><TweenNumber :value="b.done" />/{{ b.runs.length }}</span>
                <span v-if="b.awaiting" class="human">{{ b.awaiting }} chờ duyệt</span>
                <span v-else-if="b.active" class="run">{{ b.active }} đang chạy</span>
                <span v-if="b.failed" class="bad">{{ b.failed }} lỗi</span>
              </div>
              <ChevronRight :size="16" class="chev" />
            </RouterLink>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stack {
  max-width: 880px;
}
.form {
  display: grid;
  padding: 6px 20px 18px;
}
.row {
  display: grid;
  grid-template-columns: 170px 1fr;
  gap: 16px;
  padding: 12px 0;
  align-items: start;
}
.row + .row {
  border-top: 0.5px solid var(--separator);
}
.lbl {
  padding-top: 6px;
  font-weight: 500;
  color: var(--text-2);
}
.ctl {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
}
.ctl.col {
  display: grid;
  gap: 8px;
}
.recipe-pick select {
  flex: 1;
  max-width: 420px;
}
select.field {
  appearance: auto;
}
.narrow {
  max-width: 220px;
}
.mono-ish {
  font-size: 13.5px;
  line-height: 1.6;
}
.count {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
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
.seg button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-weight: 600;
}
.seg button:disabled {
  opacity: 0.4;
  cursor: default;
}
.submit {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 14px;
  padding-top: 14px;
  border-top: 0.5px solid var(--separator);
}
.warn {
  color: var(--st-waiting);
}
.list {
  display: grid;
  gap: 10px;
}
.batches {
  display: grid;
  gap: 8px;
}
.batch {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  color: inherit;
  text-decoration: none;
  transition:
    transform var(--t-fast) var(--ease-spring),
    box-shadow var(--t-base) var(--ease-out);
}
.batch:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.batch:active {
  transform: scale(0.995);
}
.b-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 3px;
}
.b-title {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar {
  position: relative;
  height: 5px;
  margin-top: 4px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-active);
}
.bar span {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 100%;
  transform-origin: left;
  transition: transform 420ms var(--ease-out);
}
.seg-ok {
  background: var(--st-ok);
}
.seg-bad {
  background: var(--st-fail);
}
.b-stats {
  display: grid;
  justify-items: end;
  gap: 2px;
  font-size: var(--fs-callout);
}
.ok {
  font-weight: 650;
}
.run {
  color: var(--st-running);
}
.human {
  color: var(--st-human);
  font-weight: 600;
}
.bad {
  color: var(--st-fail);
}
.chev {
  color: var(--text-3);
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
