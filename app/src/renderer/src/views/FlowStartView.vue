<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, LoaderCircle, Play } from '@lucide/vue'
import type { BlobRef, WorkflowDef, WorkflowInputSpec } from '@/api/types'
import { NO_PROJECT } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import PresetSelect from '@/components/PresetSelect.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import { chainNames, isBlobType } from '@/composables/flows'
import { useFlows } from '@/stores/flows'
import { useRecipes } from '@/stores/recipes'
import { useProjects } from '@/stores/projects'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ id: string }>()
const flows = useFlows()
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

const isNum = (t: string): boolean => t === 'int' || t === 'float'
const label = (name: string, spec: WorkflowInputSpec): string => spec.label || name
const entries = computed(() => Object.entries(def.value?.inputs ?? {}))
const chain = computed(() => (def.value ? chainNames(def.value, recipes.byId) : []))

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

async function submit(): Promise<void> {
  if (!def.value || submitting.value) return
  const ok = entries.value.map(([n, s]) => check(n, s)).every(Boolean)
  if (!ok) return
  const inputs: Record<string, unknown> = {}
  for (const [name, spec] of entries.value) {
    if (isBlobType(spec.type)) {
      if (blobs[name]?.length) inputs[name] = blobs[name]![0]!.hash
    } else if (isNum(spec.type)) inputs[name] = Number(values[name])
    else if (values[name] !== '') inputs[name] = values[name]
  }
  submitting.value = true
  try {
    const row = await flows.start(def.value.id, {
      inputs,
      project_id: projectId.value === NO_PROJECT ? undefined : projectId.value,
      title: title.value.trim() || undefined
    })
    void router.push(`/flows/runs/${row.id}`)
  } catch (e) {
    toasts.error(e, 'Không chạy được workflow')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="view">
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
          <div v-for="[name, spec] in entries" :key="name" class="f">
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
          <button type="submit" class="btn btn-primary btn-lg" :disabled="submitting">
            <LoaderCircle v-if="submitting" class="spin" />
            <Play v-else />
            Chạy workflow
          </button>
        </div>
      </form>
      <p v-else class="caption">Đang tải…</p>
    </div>
  </div>
</template>

<style scoped>
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
}
.chk {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
</style>
