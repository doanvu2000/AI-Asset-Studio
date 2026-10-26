<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { Archive, ArchiveRestore, ArrowLeft, Check, FolderKanban, Images, Languages, ListChecks, LoaderCircle, Plus, RotateCw, Save, X } from '@lucide/vue'
import type { BlobRef, PixelStyle, ProjectStyle } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobDropZone from '@/components/BlobDropZone.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import PresetSelect from '@/components/PresetSelect.vue'
import RunRowItem from '@/components/RunRowItem.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import { useProjects } from '@/stores/projects'
import { useRuns } from '@/stores/runs'
import { useVersions } from '@/stores/versions'
import { useRecipes } from '@/stores/recipes'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useUnsavedChanges } from '@/composables/unsaved'
import { normalizeStyle } from '@/composables/projectStyle'

const props = defineProps<{ id: string }>()
const projects = useProjects()
const runs = useRuns()
const versions = useVersions()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

const project = computed(() => projects.byId(props.id))
const loadError = ref<string | null>(null)
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    await Promise.all([projects.refresh(props.id), runs.loadProject(props.id), versions.loaded ? undefined : versions.load(), recipes.load()])
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(
  () => conn.transport,
  (t) => t && void load(),
  { immediate: true }
)

interface Draft {
  name: string
  description: string
  style: ProjectStyle
  pixelOn: boolean
  // null = not locked: the recipe's own default applies.
  pixel: { [K in keyof PixelStyle]-?: Exclude<PixelStyle[K], undefined> }
  defaults: Array<{ id: number; key: string; value: string }>
}

let rowSeq = 0
const PIXEL_SIZES = [32, 48, 64, 96, 128]
const PIXEL_COLORS = [8, 12, 16, 24, 32]
const PALETTES = ['auto', 'pico8', 'sweetie16', 'db16', 'db32', 'endesga32']
const OUTLINES: Array<[string, string]> = [
  ['dark', 'Tối theo màu'],
  ['#000000', 'Đen'],
  ['none', 'Không']
]
const DITHERS: Array<[string, string]> = [
  ['none', 'Tắt'],
  ['bayer', 'Bayer']
]

function toDraft(): Draft | null {
  const p = project.value
  if (!p) return null
  const style = normalizeStyle(p.style)
  return {
    name: p.name,
    description: p.description,
    style,
    pixelOn: !!style.pixel,
    pixel: {
      size: style.pixel?.size ?? null,
      palette: style.pixel?.palette ?? null,
      colors: style.pixel?.colors ?? null,
      outline: style.pixel?.outline ?? null,
      dither: style.pixel?.dither ?? null
    },
    defaults: Object.entries(style.inputs).map(([key, value]) => ({ id: ++rowSeq, key, value: typeof value === 'string' ? value : JSON.stringify(value) }))
  }
}

const draft = ref<Draft | null>(null)
const baseline = ref('')
const paletteBlob = ref<BlobRef[]>([])
const referenceBlob = ref<BlobRef[]>([])
const asRef = (hash: string | null): BlobRef[] => (hash ? [{ hash, mime: 'image/png', size: 0 }] : [])

function resetDraft(): void {
  draft.value = toDraft()
  paletteBlob.value = asRef(draft.value?.style.palette_image ?? null)
  referenceBlob.value = asRef(draft.value?.style.reference_image ?? null)
  baseline.value = draft.value ? JSON.stringify(payload()) : ''
}
// A WS update for this project replaces the form only while the user has nothing unsaved; summary refreshes
// and other identity-only changes leave the form alone.
const projectSig = computed(() => {
  const p = project.value
  return p ? JSON.stringify([p.id, p.updated_at, p.name, p.description, p.style]) : ''
})
watch(projectSig, (sig, prevSig) => {
  if (!sig) return
  const sameId = !!prevSig && JSON.parse(prevSig)[0] === project.value?.id
  if (!draft.value || !sameId || !dirty.value) resetDraft()
}, { immediate: true })

function parseValue(raw: string): unknown {
  const t = raw.trim()
  if (t === 'true' || t === 'false') return t === 'true'
  if (t !== '' && Number.isFinite(Number(t))) return Number(t)
  if (/^[[{]/.test(t)) {
    try {
      return JSON.parse(t)
    } catch {
      return raw
    }
  }
  return raw
}

function payload(): { name: string; description: string; style: ProjectStyle } | null {
  const d = draft.value
  if (!d) return null
  const inputs: Record<string, unknown> = {}
  for (const row of d.defaults) if (row.key.trim()) inputs[row.key.trim()] = parseValue(row.value)
  return {
    name: d.name.trim(),
    description: d.description.trim(),
    style: {
      ...d.style,
      prompt_prefix: d.style.prompt_prefix.trim(),
      prompt_suffix: d.style.prompt_suffix.trim(),
      negative: d.style.negative.trim(),
      preset: d.style.preset || null,
      pixel: d.pixelOn ? { ...d.pixel } : null,
      palette_image: paletteBlob.value[0]?.hash ?? null,
      reference_image: referenceBlob.value[0]?.hash ?? null,
      inputs
    }
  }
}

const dirty = computed(() => !!draft.value && JSON.stringify(payload()) !== baseline.value)
const saving = ref(false)
const savedAt = ref<number | null>(null)
const nameError = computed(() => (draft.value && !draft.value.name.trim() ? 'Tên dự án không được để trống' : null))
const dupKeys = computed(() => {
  const seen = new Set<string>()
  const dup = new Set<string>()
  for (const r of draft.value?.defaults ?? []) {
    const k = r.key.trim()
    if (k && seen.has(k)) dup.add(k)
    seen.add(k)
  }
  return dup
})
useUnsavedChanges(computed(() => dirty.value && !saving.value))
onBeforeRouteLeave(() => !dirty.value || saving.value || window.confirm('Dự án có thay đổi chưa lưu. Bỏ các thay đổi này?'))

async function save(): Promise<void> {
  const body = payload()
  if (!body || saving.value || nameError.value || dupKeys.value.size) return
  saving.value = true
  try {
    await projects.patch(props.id, body)
    resetDraft()
    savedAt.value = Date.now()
    toasts.push('Đã lưu dự án', 'success', 2000)
  } catch (e) {
    toasts.error(e, 'Không lưu được dự án')
  } finally {
    saving.value = false
  }
}

const VI = /[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]/i
const hasVi = (s: unknown): boolean => typeof s === 'string' && VI.test(s)
type TextKey = 'prompt_prefix' | 'prompt_suffix' | 'negative'
const TEXT_FIELDS: Array<[TextKey, string, string]> = [
  ['prompt_prefix', 'Tiền tố prompt', 'pixel art, 16-bit'],
  ['prompt_suffix', 'Hậu tố prompt', 'soft greens, whimsical forest fantasy'],
  ['negative', 'Negative prompt', 'blurry, text, watermark']
]
const translating = ref<TextKey | null>(null)
async function translate(key: TextKey): Promise<void> {
  const d = draft.value
  if (!d || translating.value) return
  const text = d.style[key]
  translating.value = key
  try {
    const out = (await api().translate([text])).texts?.[0]
    if (typeof out !== 'string' || !out.trim()) throw new Error('Core không trả bản dịch')
    if (d.style[key] === text) d.style[key] = out
  } catch (e) {
    toasts.error(e, 'Không dịch được')
  } finally {
    translating.value = null
  }
}

// Names the user can lock a default for: every input any recipe declares, minus the ones the style fields cover.
const STYLE_KEYS = new Set(['prompt', 'idea', 'style', 'negative', 'preset', 'pixel', 'palette', 'colors', 'outline', 'dither', 'palette_image', 'image'])
const inputNames = computed(() => {
  const m = new Map<string, string>()
  for (const r of recipes.list) for (const [k, s] of Object.entries(r.inputs)) if (!STYLE_KEYS.has(k) && !m.has(k)) m.set(k, s.label ?? k)
  return [...m].sort((a, b) => a[0].localeCompare(b[0]))
})
function addDefault(): void {
  draft.value?.defaults.push({ id: ++rowSeq, key: '', value: '' })
}
function removeDefault(i: number): void {
  draft.value?.defaults.splice(i, 1)
}

type Tab = 'style' | 'runs' | 'gallery'
const tab = ref<Tab>('style')
const projectRuns = computed(() => runs.runList.filter((r) => r.project_id === props.id))
const projectVersions = computed(() => versions.list.filter((v) => v.project_id === props.id && v.status !== 'rejected'))
const summary = computed(() => projects.summaryOf(props.id))

const isActive = computed(() => projects.activeId === props.id)
function toggleActive(): void {
  projects.setActive(isActive.value ? null : props.id)
}

const archiving = ref(false)
async function setArchived(on: boolean): Promise<void> {
  if (archiving.value || !project.value) return
  if (on && !window.confirm(`Lưu trữ dự án “${project.value.name}”? Lượt chạy và asset vẫn giữ nguyên, khôi phục được bất cứ lúc nào.`)) return
  archiving.value = true
  try {
    if (on) await projects.archive(props.id)
    else await projects.patch(props.id, { archived: false })
    toasts.push(on ? 'Đã lưu trữ dự án' : 'Đã khôi phục dự án', 'success', 2200)
  } catch (e) {
    toasts.error(e, on ? 'Không lưu trữ được' : 'Không khôi phục được')
  } finally {
    archiving.value = false
  }
}

function newAsset(): void {
  projects.setActive(props.id)
  void router.push({ path: '/new', query: { project: props.id } })
}
</script>

<template>
  <div class="view">
    <PageHeader :title="project?.name ?? 'Dự án'">
      <template #leading>
        <RouterLink to="/projects" class="btn btn-plain btn-icon" aria-label="Về danh sách dự án" title="Về danh sách dự án"><ArrowLeft /></RouterLink>
      </template>
      <template v-if="project" #subtitle>
        <template v-if="project.archived">Đã lưu trữ · </template>
        Cập nhật <RelTime :ts="project.updated_at" />
      </template>
      <template v-if="project">
        <button type="button" class="btn btn-plain" :aria-pressed="isActive" :title="isActive ? 'Bỏ chọn: lượt chạy mới không thuộc dự án' : 'Lượt chạy mới mặc định thuộc dự án này'" @click="toggleActive">
          <Check v-if="isActive" /> <FolderKanban v-else />
          {{ isActive ? 'Đang chọn' : 'Chọn làm dự án hiện tại' }}
        </button>
        <button v-if="!project.archived" type="button" class="btn btn-plain" :disabled="archiving" @click="setArchived(true)"><Archive /> Lưu trữ</button>
        <button v-else type="button" class="btn btn-plain" :disabled="archiving" @click="setArchived(false)"><ArchiveRestore /> Khôi phục</button>
        <button type="button" class="btn btn-primary" @click="newAsset"><Plus /> Tạo asset trong dự án</button>
      </template>
    </PageHeader>

    <div class="scroll">
      <p v-if="!project && loading" class="caption state" role="status"><LoaderCircle class="spin" :size="14" /> Đang tải dự án…</p>
      <EmptyState v-else-if="!project" :icon="FolderKanban" :title="loadError ? 'Không tải được dự án' : 'Không tìm thấy dự án'" :text="loadError ?? undefined">
        <button v-if="loadError" class="btn" @click="load"><RotateCw /> Thử lại</button>
        <RouterLink to="/projects" class="btn">Về danh sách dự án</RouterLink>
      </EmptyState>

      <div v-else class="page">
        <dl class="stats card">
          <div>
            <dt class="caption">Lượt chạy</dt>
            <dd class="num"><TweenNumber :value="summary?.runs_total ?? projectRuns.length" /></dd>
          </div>
          <div>
            <dt class="caption">Đang chạy</dt>
            <dd class="num run"><TweenNumber :value="(summary?.runs.running ?? 0) + (summary?.runs.awaiting_human ?? 0)" /></dd>
          </div>
          <div>
            <dt class="caption">Đã duyệt</dt>
            <dd class="num ok"><TweenNumber :value="summary?.approved_versions ?? 0" /></dd>
          </div>
          <div>
            <dt class="caption">Nháp chờ duyệt</dt>
            <dd class="num"><TweenNumber :value="summary?.versions.draft ?? 0" /></dd>
          </div>
          <div>
            <dt class="caption">Hoạt động gần nhất</dt>
            <dd class="when"><RelTime :ts="Math.max(summary?.last_activity ?? 0, project.updated_at)" /></dd>
          </div>
        </dl>

        <div class="segmented tabs" role="tablist" aria-label="Phần của dự án">
          <button role="tab" :aria-selected="tab === 'style'" :class="{ on: tab === 'style' }" @click="tab = 'style'">
            Phong cách <span v-if="dirty" class="dirty-dot" aria-label="chưa lưu" />
          </button>
          <button role="tab" :aria-selected="tab === 'runs'" :class="{ on: tab === 'runs' }" @click="tab = 'runs'">
            <ListChecks :size="13" /> Lượt chạy <span class="num c">{{ projectRuns.length }}</span>
          </button>
          <button role="tab" :aria-selected="tab === 'gallery'" :class="{ on: tab === 'gallery' }" @click="tab = 'gallery'">
            <Images :size="13" /> Thư viện <span class="num c">{{ projectVersions.length }}</span>
          </button>
        </div>

        <form v-if="tab === 'style' && draft" class="style" novalidate @submit.prevent="save">
          <div class="fields card">
            <div class="f">
              <label for="p-name" class="f-label">Tên dự án</label>
              <div class="f-control">
                <input id="p-name" v-model="draft.name" class="field" maxlength="120" :aria-invalid="!!nameError" />
                <p v-if="nameError" class="err" role="alert">{{ nameError }}</p>
              </div>
            </div>
            <div class="f">
              <label for="p-desc" class="f-label">Mô tả</label>
              <div class="f-control">
                <textarea id="p-desc" v-model="draft.description" class="field" rows="2" maxlength="4000" placeholder="Game nào, bộ asset gồm gì…" />
              </div>
            </div>
          </div>

          <h2 class="section-label">Phong cách khoá cho mọi lượt chạy</h2>
          <p class="caption lead">
            Chỉ áp vào đầu vào bạn để trống khi tạo asset; giá trị bạn tự chọn trong form luôn được giữ. Văn bản nên bằng tiếng Anh.
          </p>
          <div class="fields card">
            <div v-for="[key, lbl, ph] in TEXT_FIELDS" :key="key" class="f">
              <label :for="`p-${key}`" class="f-label">{{ lbl }}</label>
              <div class="f-control">
                <textarea :id="`p-${key}`" v-model="draft.style[key]" class="field" rows="2" :placeholder="`Ví dụ: ${ph}`" spellcheck="false" />
                <div v-if="hasVi(draft.style[key])" class="vi-row">
                  <span class="caption">Model ảnh hiểu tiếng Anh tốt hơn.</span>
                  <button type="button" class="btn btn-plain small" :disabled="!!translating" @click="translate(key)">
                    <LoaderCircle v-if="translating === key" class="spin" :size="14" />
                    <Languages v-else :size="14" />
                    Dịch sang tiếng Anh
                  </button>
                </div>
                <p v-else-if="key === 'prompt_prefix'" class="caption help">Thêm vào đầu prompt / ý tưởng, hoặc vào ô "phong cách" của quy trình brief.</p>
              </div>
            </div>
            <div class="f">
              <label for="p-preset" class="f-label">Preset xuất</label>
              <div class="f-control">
                <PresetSelect id="p-preset" :model-value="draft.style.preset ?? ''" empty-label="Không khoá preset" @update:model-value="(v) => draft && (draft.style.preset = v ? String(v) : null)" />
              </div>
            </div>
            <div class="f">
              <span class="f-label">Pixel art</span>
              <div class="f-control">
                <label class="switch-row">
                  <span class="switch">
                    <input v-model="draft.pixelOn" type="checkbox" role="switch" aria-label="Khoá pixel art" />
                    <span class="track"><span class="knob" /></span>
                  </span>
                  <span class="callout">{{ draft.pixelOn ? 'Khoá thông số pixel art cho quy trình pixel' : 'Không khoá' }}</span>
                </label>
                <Transition name="fold">
                  <div v-if="draft.pixelOn" class="pixel-grid">
                    <span class="caption">Cỡ (px)</span>
                    <div class="segmented small" role="radiogroup" aria-label="Cỡ pixel art">
                      <button type="button" role="radio" :aria-checked="draft.pixel.size === null" :class="{ on: draft.pixel.size === null }" title="Dùng mặc định của quy trình" @click="draft.pixel.size = null">Không khoá</button>
                      <button v-for="o in PIXEL_SIZES" :key="o" type="button" role="radio" class="num" :aria-checked="draft.pixel.size === o" :class="{ on: draft.pixel.size === o }" @click="draft.pixel.size = o">{{ o }}</button>
                    </div>
                    <span class="caption">Số màu</span>
                    <div class="segmented small" role="radiogroup" aria-label="Số màu">
                      <button type="button" role="radio" :aria-checked="draft.pixel.colors === null" :class="{ on: draft.pixel.colors === null }" title="Dùng mặc định của quy trình" @click="draft.pixel.colors = null">Không khoá</button>
                      <button v-for="o in PIXEL_COLORS" :key="o" type="button" role="radio" class="num" :aria-checked="draft.pixel.colors === o" :class="{ on: draft.pixel.colors === o }" @click="draft.pixel.colors = o">{{ o }}</button>
                    </div>
                    <span class="caption">Bảng màu</span>
                    <div class="segmented small" role="radiogroup" aria-label="Bảng màu">
                      <button type="button" role="radio" :aria-checked="draft.pixel.palette === null" :class="{ on: draft.pixel.palette === null }" title="Dùng mặc định của quy trình" @click="draft.pixel.palette = null">Không khoá</button>
                      <button v-for="o in PALETTES" :key="o" type="button" role="radio" :aria-checked="draft.pixel.palette === o" :class="{ on: draft.pixel.palette === o }" @click="draft.pixel.palette = o">{{ o === 'auto' ? 'Tự động' : o }}</button>
                    </div>
                    <span class="caption">Viền</span>
                    <div class="segmented small" role="radiogroup" aria-label="Viền ngoài">
                      <button type="button" role="radio" :aria-checked="draft.pixel.outline === null" :class="{ on: draft.pixel.outline === null }" title="Dùng mặc định của quy trình" @click="draft.pixel.outline = null">Không khoá</button>
                      <button v-for="[o, l] in OUTLINES" :key="o" type="button" role="radio" :aria-checked="draft.pixel.outline === o" :class="{ on: draft.pixel.outline === o }" @click="draft.pixel.outline = o">{{ l }}</button>
                    </div>
                    <span class="caption">Dither</span>
                    <div class="segmented small" role="radiogroup" aria-label="Dither">
                      <button type="button" role="radio" :aria-checked="draft.pixel.dither === null" :class="{ on: draft.pixel.dither === null }" title="Dùng mặc định của quy trình" @click="draft.pixel.dither = null">Không khoá</button>
                      <button v-for="[o, l] in DITHERS" :key="o" type="button" role="radio" :aria-checked="draft.pixel.dither === o" :class="{ on: draft.pixel.dither === o }" @click="draft.pixel.dither = o">{{ l }}</button>
                    </div>
                  </div>
                </Transition>
              </div>
            </div>
            <div class="f">
              <span class="f-label">Ảnh lấy bảng màu</span>
              <div class="f-control">
                <BlobDropZone v-model="paletteBlob" kind="image" label="Ảnh lấy bảng màu" />
                <div class="blob-foot">
                  <span class="caption">Quy trình pixel lấy màu từ ảnh này thay cho bảng màu dựng sẵn.</span>
                  <button v-if="paletteBlob.length" type="button" class="btn btn-plain small" @click="paletteBlob = []"><X :size="13" /> Bỏ ảnh</button>
                </div>
              </div>
            </div>
            <div class="f">
              <span class="f-label">Ảnh tham chiếu</span>
              <div class="f-control">
                <BlobDropZone v-model="referenceBlob" kind="image" label="Ảnh tham chiếu" />
                <div class="blob-foot">
                  <span class="caption">Điền vào đầu vào "ảnh" của quy trình khi bạn không thả ảnh khác.</span>
                  <button v-if="referenceBlob.length" type="button" class="btn btn-plain small" @click="referenceBlob = []"><X :size="13" /> Bỏ ảnh</button>
                </div>
              </div>
            </div>
            <div class="f">
              <span class="f-label">Đầu vào mặc định</span>
              <div class="f-control">
                <div v-auto-animate class="defaults">
                  <div v-for="(row, i) in draft.defaults" :key="row.id" class="def-row">
                    <input v-model="row.key" class="field mono" list="p-input-names" placeholder="tên đầu vào, vd elevation" :aria-label="`Tên đầu vào ${i + 1}`" spellcheck="false" :aria-invalid="dupKeys.has(row.key.trim())" />
                    <input v-model="row.value" class="field mono" placeholder="giá trị, vd 45" :aria-label="`Giá trị ${i + 1}`" spellcheck="false" />
                    <button type="button" class="btn btn-plain btn-icon" :aria-label="`Bỏ dòng ${i + 1}`" @click="removeDefault(i)"><X /></button>
                  </div>
                </div>
                <datalist id="p-input-names">
                  <option v-for="[k, l] in inputNames" :key="k" :value="k">{{ l }}</option>
                </datalist>
                <p v-if="dupKeys.size" class="err" role="alert">Trùng tên: {{ [...dupKeys].join(', ') }}</p>
                <button type="button" class="btn btn-plain small add" @click="addDefault"><Plus :size="13" /> Thêm đầu vào</button>
                <p class="caption help">Số, true/false hoặc chữ. Quy trình không có đầu vào trùng tên sẽ bỏ qua dòng đó.</p>
              </div>
            </div>
            <div class="f">
              <label for="p-notes" class="f-label">Ghi chú</label>
              <div class="f-control">
                <textarea id="p-notes" v-model="draft.style.notes" class="field" rows="3" placeholder="Quy ước tên, kích thước, bảng màu… (chỉ để đọc, không áp vào lượt chạy)" />
              </div>
            </div>
          </div>

          <div class="savebar" :class="{ dirty }">
            <Transition name="fade" mode="out-in">
              <span v-if="saving" key="s" class="caption st" role="status"><LoaderCircle class="spin" :size="13" /> Đang lưu…</span>
              <span v-else-if="dirty" key="d" class="caption st warn" role="status">Có thay đổi chưa lưu</span>
              <span v-else-if="savedAt" key="ok" class="caption st ok" role="status"><Check :size="13" /> Đã lưu</span>
              <span v-else key="n" class="caption st">Không có thay đổi</span>
            </Transition>
            <button type="button" class="btn btn-plain" :disabled="!dirty || saving" @click="resetDraft">Hoàn tác</button>
            <button type="submit" class="btn btn-primary" :disabled="!dirty || saving || !!nameError || dupKeys.size > 0"><Save /> Lưu dự án</button>
          </div>
        </form>

        <section v-else-if="tab === 'runs'" class="list card">
          <EmptyState v-if="!projectRuns.length" :icon="ListChecks" title="Dự án chưa có lượt chạy" text="Lượt chạy tạo trong dự án sẽ tự mang phong cách đã khoá.">
            <button class="btn btn-primary" @click="newAsset"><Plus /> Tạo asset trong dự án</button>
          </EmptyState>
          <div v-else v-auto-animate>
            <div v-for="r in projectRuns.slice(0, 200)" :key="r.id" class="lrow"><RunRowItem :run="r" /></div>
          </div>
          <RouterLink v-if="projectRuns.length" :to="{ path: '/runs', query: { project: id } }" class="more-link">Xem trong Lượt chạy</RouterLink>
        </section>

        <section v-else-if="tab === 'gallery'" class="gal">
          <EmptyState v-if="!projectVersions.length" :icon="Images" title="Chưa có asset nào" text="Lượt chạy xong sẽ thêm bản nháp vào đây.">
            <button class="btn btn-primary" @click="newAsset"><Plus /> Tạo asset trong dự án</button>
          </EmptyState>
          <template v-else>
            <div class="vgrid">
              <RouterLink v-for="v in projectVersions.slice(0, 120)" :key="v.id" :to="`/runs/${v.run_id}`" class="vtile" :class="`vs-${v.status}`" :title="v.asset_name">
                <BlobThumb :blob="v.blob" :size="256" />
                <span class="vname">{{ v.asset_name || v.blob.name }}</span>
              </RouterLink>
            </div>
            <RouterLink :to="{ path: '/gallery', query: { project: id } }" class="more-link">Duyệt trong Thư viện</RouterLink>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 880px;
  margin: 0 auto;
  display: grid;
  gap: 16px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
  padding: 14px 18px;
}
.stats div {
  display: grid;
  gap: 2px;
}
.stats dd {
  margin: 0;
  font-size: var(--fs-title1);
  font-weight: 650;
  letter-spacing: -0.015em;
}
.stats dd.when {
  font-size: var(--fs-body);
  font-weight: 500;
  padding-top: 6px;
}
.stats dd.ok {
  color: var(--st-ok);
}
.stats dd.run {
  color: var(--st-running);
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
  padding: 0 9px;
  font-size: var(--fs-callout);
}
.segmented button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-weight: 600;
}
.tabs {
  justify-self: start;
}
.c {
  font-size: 11px;
  color: var(--text-3);
}
.dirty-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--st-waiting);
}
.style {
  display: grid;
  gap: 12px;
}
.lead {
  margin-top: -6px;
  color: var(--text-3);
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
.vi-row,
.blob-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px 8px;
  margin-top: 6px;
  color: var(--text-3);
}
.small {
  height: 24px;
  padding: 0 8px;
  font-size: var(--fs-callout);
}
.switch-row {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding-top: 3px;
}
.switch {
  display: inline-flex;
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
.pixel-grid {
  display: grid;
  grid-template-columns: 70px 1fr;
  justify-items: start;
  align-items: center;
  gap: 8px 10px;
  margin-top: 12px;
}
.fold-enter-active,
.fold-leave-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.fold-enter-from,
.fold-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
.defaults {
  display: grid;
  gap: 6px;
}
.def-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 6px;
}
.add {
  margin-top: 6px;
}
.savebar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin: 0 -24px;
  padding: 12px 24px 16px;
  border-top: 0.5px solid var(--separator);
  background: color-mix(in srgb, var(--bg-content) 88%, transparent);
  backdrop-filter: saturate(1.6) blur(16px);
}
.st {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-right: auto;
}
.st.warn {
  color: var(--st-waiting);
}
.st.ok {
  color: var(--st-ok);
}
.list {
  padding: 4px 8px;
}
.lrow + .lrow {
  border-top: 0.5px solid var(--separator);
}
.more-link {
  display: inline-block;
  margin: 10px 8px 6px;
  color: var(--accent);
  font-weight: 500;
  text-decoration: none;
}
.more-link:hover {
  text-decoration: underline;
}
.gal {
  display: grid;
  gap: 8px;
}
.vgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.vtile {
  display: grid;
  gap: 6px;
  color: var(--text);
  text-decoration: none;
}
.vtile :deep(.blob),
.vtile :deep(.glyph) {
  border-radius: var(--r-md);
  box-shadow: var(--hairline);
  transition: box-shadow var(--t-base) var(--ease-out);
}
.vtile:hover :deep(.blob),
.vtile:hover :deep(.glyph) {
  box-shadow: 0 0 0 2px var(--accent);
}
.vtile.vs-approved :deep(.blob) {
  box-shadow: inset 0 0 0 1.5px var(--st-ok);
}
.vname {
  font-size: var(--fs-callout);
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spin {
  animation: spin 1s linear infinite;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .page,
  .fold-enter-active,
  .fold-leave-active {
    animation: none;
    transition: none;
  }
}
</style>
