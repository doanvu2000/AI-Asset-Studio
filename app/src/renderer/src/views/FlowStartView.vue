<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, FolderOpen, ImagePlus, Layers, LoaderCircle, Play } from '@lucide/vue'
import type { BlobRef, WorkflowDef, WorkflowInputSpec } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import FitBadge from '@/components/FitBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import PresetSelect from '@/components/PresetSelect.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import { chainNames, isBlobType } from '@/composables/flows'
import { settlePool } from '@/composables/pool'
import { useFlows } from '@/stores/flows'
import { useFit } from '@/stores/fit'
import { useRecipes } from '@/stores/recipes'
import { useProjects } from '@/stores/projects'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ id: string }>()
const flows = useFlows()
const fit = useFit()
void fit.load()
const recipes = useRecipes()
const projects = useProjects()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

const def = ref<WorkflowDef | null>(null)
const loadError = ref<string | null>(null)
async function load(): Promise<void> {
  try {
    def.value = await api().getFlow(props.id)
    loadError.value = null
  } catch (e) {
    loadError.value = (e as Error).message
  }
}
watch(
  () => conn.transport,
  (t) => {
    if (!t) return
    void recipes.load()
    void load()
    if (!projects.loaded && !projects.loading) void projects.load().catch(() => undefined)
  },
  { immediate: true }
)

type Value = string | number | boolean
const values = reactive<Record<string, Value>>({})
const blobs = reactive<Record<string, BlobRef[]>>({})
const errors = reactive<Record<string, string | null>>({})
const title = ref('')
const projectId = ref<string>(projects.activeId ?? NO_PROJECT)
const submitting = ref(false)
const defFit = computed(() => (def.value ? fit.flow(def.value.id) : null))
// Red asks once instead of blocking: the estimate can be wrong for the user's setup.
const riskAck = ref(false)
watch([() => def.value?.id, () => defFit.value?.level], () => (riskAck.value = false))

const MAX_BATCH = 200
const UPLOAD_CONCURRENCY = 4
type Source = 'images' | 'prompts'
const batchMode = ref(false)
const source = ref<Source>('images')
const files = ref<File[]>([])
const promptText = ref('')
const uploaded = ref(0)
const folderInput = ref<HTMLInputElement | null>(null)
const filesInput = ref<HTMLInputElement | null>(null)

const isNum = (t: string): boolean => t === 'int' || t === 'float'
const label = (name: string, spec: WorkflowInputSpec): string => spec.label || name
const entries = computed(() => Object.entries(def.value?.inputs ?? {}))
const chain = computed(() => (def.value ? chainNames(def.value, recipes.byId) : []))

const imageInput = computed(() => entries.value.find(([, s]) => s.type === 'image' || s.type === 'images')?.[0] ?? null)
const promptInput = computed(() => entries.value.find(([, s]) => s.type === 'text')?.[0] ?? null)
const canBatch = computed(() => !!imageInput.value || !!promptInput.value)
const targetName = computed(() => (source.value === 'images' ? imageInput.value : promptInput.value))
const formEntries = computed(() => (batchMode.value ? entries.value.filter(([n]) => n !== targetName.value) : entries.value))
const prompts = computed(() =>
  promptText.value
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
)
const itemCount = computed(() => (source.value === 'images' ? files.value.length : prompts.value.length))
const overLimit = computed(() => itemCount.value > MAX_BATCH)

const IMAGE_EXT = /\.(png|jpe?g|webp)$/i
function takeFiles(e: Event): void {
  const el = e.target as HTMLInputElement
  const picked = [...(el.files ?? [])].filter((f) => IMAGE_EXT.test(f.name))
  el.value = ''
  const key = (f: File): string => f.webkitRelativePath || f.name
  files.value = picked.sort((a, b) => key(a).localeCompare(key(b), undefined, { numeric: true }))
}
const stem = (name: string): string => name.replace(/\.[^.]+$/, '')

watch(
  def,
  (d) => {
    for (const [name, spec] of Object.entries(d?.inputs ?? {})) {
      if (isBlobType(spec.type)) blobs[name] = []
      else values[name] = (spec.default as Value | undefined) ?? (spec.type === 'bool' ? false : isNum(spec.type) ? 0 : '')
    }
  },
  { immediate: true }
)
watch(
  [imageInput, promptInput],
  () => {
    if (source.value === 'images' && !imageInput.value && promptInput.value) source.value = 'prompts'
    else if (source.value === 'prompts' && !promptInput.value && imageInput.value) source.value = 'images'
  },
  { immediate: true }
)

function check(name: string, spec: WorkflowInputSpec): boolean {
  const need = spec.required !== false && (spec.default === null || spec.default === undefined)
  let err: string | null = null
  if (isBlobType(spec.type)) {
    if (need && !blobs[name]?.length) err = 'Cần thêm file'
  } else if (isNum(spec.type)) {
    const v = Number(values[name])
    if (Number.isNaN(v)) err = 'Nhập một số'
    else if (spec.type === 'int' && !Number.isInteger(v)) err = 'Nhập số nguyên'
  } else if (spec.type === 'text' && need && !String(values[name] ?? '').trim()) err = 'Không được để trống'
  errors[name] = err
  return !err
}

function collect(list: [string, WorkflowInputSpec][]): Record<string, unknown> {
  const inputs: Record<string, unknown> = {}
  for (const [name, spec] of list) {
    if (isBlobType(spec.type)) {
      if (blobs[name]?.length) inputs[name] = blobs[name]![0]!.hash
    } else if (isNum(spec.type)) inputs[name] = Number(values[name])
    else if (values[name] !== '') inputs[name] = values[name]
  }
  return inputs
}

const projectArg = (): string | undefined => (projectId.value === NO_PROJECT ? undefined : projectId.value)

async function submitBatch(): Promise<void> {
  const target = targetName.value
  const spec = def.value?.inputs[target ?? '']
  if (!def.value || !target || !spec) return
  if (!itemCount.value) return void toasts.push(source.value === 'images' ? 'Chọn thư mục hoặc ảnh trước' : 'Nhập ít nhất một dòng prompt', 'error', 2500)
  if (overLimit.value) return void toasts.push(`Một lô tối đa ${MAX_BATCH} lượt`, 'error', 2500)
  if (!formEntries.value.map(([n, s]) => check(n, s)).every(Boolean)) return
  submitting.value = true
  uploaded.value = 0
  try {
    let items: { inputs: Record<string, unknown>; title?: string }[]
    if (source.value === 'images') {
      const res = await settlePool(files.value, UPLOAD_CONCURRENCY, async (f) => {
        const ref = await api().uploadBlob(f)
        uploaded.value += 1
        return ref
      })
      const bad = res.filter((r) => r.status === 'rejected').length
      if (bad) throw new Error(`${bad} ảnh tải lên thất bại`)
      items = res.map((r, i) => {
        const hash = (r as PromiseFulfilledResult<BlobRef>).value.hash
        return { inputs: { [target]: spec.type === 'images' ? [hash] : hash }, title: stem(files.value[i]!.name) }
      })
    } else items = prompts.value.map((p) => ({ inputs: { [target]: p } }))
    const batch = await api().startFlowBatch({
      flow_id: def.value.id,
      items,
      shared_params: collect(formEntries.value),
      project_id: projectArg(),
      title: title.value.trim() || undefined
    })
    void router.push(`/flows/batches/${batch.id}`)
  } catch (e) {
    toasts.error(e, 'Không chạy được lô workflow')
  } finally {
    submitting.value = false
  }
}

async function submit(): Promise<void> {
  if (!def.value || submitting.value) return
  if (defFit.value?.level === 'red' && !riskAck.value) {
    riskAck.value = true
    return
  }
  if (batchMode.value) return submitBatch()
  const ok = entries.value.map(([n, s]) => check(n, s)).every(Boolean)
  if (!ok) return
  submitting.value = true
  try {
    const row = await flows.start(def.value.id, { inputs: collect(entries.value), project_id: projectArg(), title: title.value.trim() || undefined })
    void router.push(`/flows/runs/${row.id}`)
  } catch (e) {
    toasts.error(e, 'Không chạy được workflow')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="view" @keydown.ctrl.enter.prevent="submit" @keydown.meta.enter.prevent="submit">
    <PageHeader :title="def?.title ?? 'Chạy workflow'" subtitle="Điền đầu vào rồi bấm chạy">
      <template #leading>
        <RouterLink to="/flows" class="btn btn-plain btn-icon" aria-label="Về danh sách workflow"><ArrowLeft /></RouterLink>
      </template>
    </PageHeader>
    <div class="scroll">
      <EmptyState v-if="loadError" title="Không mở được workflow" :text="loadError">
        <button class="btn" @click="load">Thử lại</button>
      </EmptyState>
      <form v-else-if="def" class="form stack" novalidate @submit.prevent="submit">
        <header class="f-head">
          <p v-if="def.description" class="callout">{{ def.description }}</p>
          <p class="caption chain">
            <span v-for="(n, i) in chain" :key="i" class="link">
              <span class="chip">{{ n }}</span>
              <ArrowRight v-if="i < chain.length - 1" :size="12" class="arr" aria-hidden="true" />
            </span>
          </p>
        </header>

        <FitBadge :entry="defFit" block />

        <div v-if="canBatch" class="seg pill-tabs" role="tablist" aria-label="Chế độ chạy">
          <button type="button" role="tab" :aria-selected="!batchMode" @click="batchMode = false"><Play /> Một lượt</button>
          <button type="button" role="tab" :aria-selected="batchMode" @click="batchMode = true"><Layers /> Hàng loạt</button>
        </div>

        <div v-if="batchMode && canBatch" class="fields card">
          <div v-if="imageInput && promptInput" class="f">
            <span class="f-label">Mỗi lượt lấy từ</span>
            <div class="seg pill-tabs" role="radiogroup" aria-label="Nguồn các lượt">
              <button type="button" role="radio" :aria-checked="source === 'images'" @click="source = 'images'">Thư mục ảnh</button>
              <button type="button" role="radio" :aria-checked="source === 'prompts'" @click="source = 'prompts'">Danh sách prompt</button>
            </div>
          </div>
          <div v-if="source === 'images'" class="f">
            <span class="f-label">Ảnh cho “{{ imageInput ? label(imageInput, def.inputs[imageInput]!) : '' }}” <span class="caption">(mỗi ảnh một lượt)</span></span>
            <div class="pick">
              <button type="button" class="btn" @click="folderInput?.click()"><FolderOpen /> Chọn thư mục</button>
              <button type="button" class="btn btn-plain" @click="filesInput?.click()"><ImagePlus /> Chọn nhiều ảnh</button>
            </div>
            <input ref="folderInput" class="hidden" type="file" webkitdirectory multiple @change="takeFiles" />
            <input ref="filesInput" class="hidden" type="file" accept="image/png,image/jpeg,image/webp" multiple @change="takeFiles" />
            <p v-if="files.length" class="caption">{{ files.slice(0, 3).map((f) => f.name).join(', ') }}{{ files.length > 3 ? '…' : '' }}</p>
          </div>
          <div v-else class="f">
            <label for="wf-prompts" class="f-label">Prompt cho “{{ promptInput ? label(promptInput, def.inputs[promptInput]!) : '' }}” <span class="caption">(mỗi dòng một lượt)</span></label>
            <textarea id="wf-prompts" v-model="promptText" class="field" rows="6" />
          </div>
          <p class="caption num count" :class="{ err: overLimit }" role="status">
            {{ itemCount }} lượt sẽ được tạo{{ overLimit ? ` — vượt giới hạn ${MAX_BATCH} lượt` : '' }}
          </p>
        </div>

        <div class="fields card">
          <div class="f">
            <span class="f-label">Dự án</span>
            <ProjectSelect v-model="projectId" label="Dự án cho lượt chạy" />
          </div>
          <div class="f">
            <label for="wf-title" class="f-label">Tên lượt chạy</label>
            <input id="wf-title" v-model="title" class="field" type="text" :placeholder="def.title" />
          </div>
          <p v-if="!entries.length" class="caption">Workflow này không cần đầu vào.</p>
          <div v-for="[name, spec] in formEntries" :key="name" class="f">
            <label :for="`in-${name}`" class="f-label">
              {{ label(name, spec) }}
              <span v-if="spec.required !== false && spec.default == null" class="req" aria-hidden="true">*</span>
            </label>
            <div class="f-control">
              <BlobDropZone
                v-if="isBlobType(spec.type)"
                :id="`in-${name}`"
                v-model="blobs[name]"
                :kind="spec.type as 'image' | 'mesh' | 'file' | 'video'"
                :label="label(name, spec)"
                :invalid="!!errors[name]"
              />
              <textarea
                v-else-if="spec.type === 'text'"
                :id="`in-${name}`"
                v-model="values[name] as string"
                class="field"
                rows="3"
                :aria-invalid="!!errors[name]"
                @blur="check(name, spec)"
              />
              <PresetSelect v-else-if="spec.type === 'preset'" :id="`in-${name}`" v-model="values[name]" />
              <input v-else-if="spec.type === 'bool'" :id="`in-${name}`" v-model="values[name]" class="chk" type="checkbox" />
              <input
                v-else-if="isNum(spec.type)"
                :id="`in-${name}`"
                v-model.number="values[name]"
                class="field num narrow"
                type="number"
                :step="spec.type === 'int' ? 1 : 0.1"
                :aria-invalid="!!errors[name]"
                @blur="check(name, spec)"
              />
              <input v-else :id="`in-${name}`" v-model="values[name] as string" class="field" type="text" />
              <p v-if="errors[name]" class="caption err" role="alert">{{ errors[name] }}</p>
            </div>
          </div>
        </div>

        <div class="actions">
          <span v-if="riskAck" class="caption risk" role="alert">Máy không đủ cấu hình cho workflow này, có thể rất chậm hoặc lỗi. Vẫn chạy?</span>
          <button type="submit" class="btn btn-primary btn-lg" :disabled="submitting">
            <LoaderCircle v-if="submitting" class="spin" />
            <Play v-else />
            {{ riskAck ? 'Vẫn chạy' : batchMode ? `Chạy ${itemCount} lượt` : 'Chạy workflow' }}
            <span v-if="submitting && batchMode && source === 'images'" class="caption num">({{ uploaded }}/{{ files.length }} ảnh)</span>
          </button>
        </div>
      </form>
      <p v-else class="caption">Đang tải…</p>
    </div>
  </div>
</template>

<style scoped>
.pill-tabs {
  justify-self: start;
}
.pick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.hidden {
  display: none;
}
.count {
  color: var(--text-2);
}
.form {
  max-width: 720px;
}
.f-head {
  display: grid;
  gap: 8px;
}
.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
}
.link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-active);
  color: var(--text-2);
  font-weight: 600;
}
.arr {
  color: var(--text-3);
}
.fields {
  display: grid;
  gap: 14px;
  padding: 18px;
}
.f {
  display: grid;
  gap: 6px;
}
.f-label {
  font-weight: 600;
}
.f-control {
  display: grid;
  gap: 6px;
}
.req {
  color: var(--st-fail);
}
.err {
  color: var(--st-fail);
}
.narrow {
  max-width: 160px;
}
.actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.risk {
  color: var(--st-fail);
  font-weight: 600;
}
.chk {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
</style>
