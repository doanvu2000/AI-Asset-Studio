<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Download, FileText, LoaderCircle, Play, Plus, RotateCw, Sparkles, Upload, X } from '@lucide/vue'
import type { ManifestFormat, ManifestRow, ManifestStatus, RecipeInfo, RecipeInputSpec } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import { useRuns } from '@/stores/runs'
import { useVersions } from '@/stores/versions'
import { useRecipes } from '@/stores/recipes'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ projectId: string; projectName: string }>()
const runs = useRuns()
const versions = useVersions()
const recipes = useRecipes()
const toasts = useToasts()

const STATUS_LABEL: Record<ManifestStatus, string> = {
  todo: 'Chưa chạy',
  running: 'Đang chạy',
  review: 'Chờ duyệt',
  approved: 'Đã duyệt',
  failed: 'Lỗi'
}
const STATUS_ORDER: ManifestStatus[] = ['todo', 'running', 'review', 'approved', 'failed']
const BLOB = new Set(['image', 'mesh', 'file', 'video'])
const GDD_LIMIT = 30

const rows = ref<ManifestRow[]>([])
const serverStatus = ref(new Map<string, ManifestStatus>())
const selected = ref(new Set<string>())
const expanded = ref(new Set<string>())
const loading = ref(false)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const v = await api().getManifest(props.projectId)
    rows.value = v.rows.map(strip)
    serverStatus.value = new Map(v.rows.map((r) => [r.id, r.status]))
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(() => props.projectId, () => void load(), { immediate: true })

function strip(r: ManifestRow): ManifestRow {
  return { id: r.id, name: r.name, recipe_id: r.recipe_id, inputs: { ...r.inputs }, notes: r.notes, run_id: r.run_id }
}

// Live from the runs/versions stores (WS-fed); the server's status covers runs the stores have not loaded.
function statusOf(r: ManifestRow): ManifestStatus {
  const run = r.run_id ? runs.runs.get(r.run_id) : undefined
  if (!run) return r.run_id ? (serverStatus.value.get(r.id) ?? 'todo') : 'todo'
  switch (run.status) {
    case 'running':
      return 'running'
    case 'awaiting_human':
      return 'review'
    case 'failed':
    case 'interrupted':
      return 'failed'
    case 'cancelled':
      return 'todo'
  }
  return versions.list.some((v) => v.run_id === run.id && v.status === 'approved') ? 'approved' : 'review'
}
const counts = computed(() => {
  const c: Partial<Record<ManifestStatus, number>> = {}
  for (const r of rows.value) c[statusOf(r)] = (c[statusOf(r)] ?? 0) + 1
  return c
})

const saving = ref(false)
const saveError = ref(false)
let saveTimer: ReturnType<typeof setTimeout> | null = null
let pending = false

async function flush(): Promise<void> {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = null
  if (!pending) return
  pending = false
  saving.value = true
  try {
    const v = await api().putManifest(props.projectId, rows.value)
    serverStatus.value = new Map(v.rows.map((r) => [r.id, r.status]))
    saveError.value = false
  } catch (e) {
    saveError.value = true
    toasts.error(e, 'Không lưu được danh sách asset')
  } finally {
    saving.value = false
  }
}
function scheduleSave(): void {
  pending = true
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => void flush(), 700)
}
onBeforeUnmount(() => void flush())

const recipeById = (id: string): RecipeInfo | undefined => recipes.byId(id)
function primaryInput(r: RecipeInfo | undefined): string | null {
  if (!r) return null
  for (const k of ['prompt', 'idea']) if (r.inputs[k]) return k
  return Object.entries(r.inputs).find(([, s]) => s.type === 'text')?.[0] ?? null
}
function extraInputs(r: RecipeInfo | undefined): Array<[string, RecipeInputSpec]> {
  if (!r) return []
  const main = primaryInput(r)
  return Object.entries(r.inputs).filter(([k, s]) => k !== main && !BLOB.has(s.type))
}
function fileInputs(r: RecipeInfo | undefined): string[] {
  if (!r) return []
  return Object.entries(r.inputs).filter(([, s]) => BLOB.has(s.type) && s.required !== false && s.default === undefined).map(([k, s]) => s.label ?? k)
}

function setInput(row: ManifestRow, key: string, value: unknown): void {
  if (value === '' || value === null || value === undefined) delete row.inputs[key]
  else row.inputs[key] = value
  scheduleSave()
}
function changeRecipe(row: ManifestRow, id: string): void {
  const before = primaryInput(recipeById(row.recipe_id))
  const text = before ? row.inputs[before] : undefined
  row.recipe_id = id
  const r = recipeById(id)
  const after = primaryInput(r)
  const kept: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(row.inputs)) if (r?.inputs[k]) kept[k] = v
  if (after && text !== undefined && kept[after] === undefined) kept[after] = text
  row.inputs = kept
  scheduleSave()
}
function addRow(): void {
  const id = crypto.randomUUID().replace(/-/g, '').slice(0, 12)
  rows.value.push({ id, name: '', recipe_id: recipes.list[0]?.id ?? '', inputs: {}, notes: '', run_id: null })
  scheduleSave()
}
function removeRow(row: ManifestRow): void {
  if (row.run_id && !window.confirm(`Bỏ “${row.name || 'dòng này'}” khỏi danh sách? Lượt chạy đã tạo vẫn giữ nguyên.`)) return
  rows.value = rows.value.filter((r) => r.id !== row.id)
  selected.value.delete(row.id)
  scheduleSave()
}
function toggle(set: Set<string>, id: string): Set<string> {
  const next = new Set(set)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}
const allSelected = computed(() => rows.value.length > 0 && rows.value.every((r) => selected.value.has(r.id)))
function toggleAll(): void {
  selected.value = allSelected.value ? new Set() : new Set(rows.value.map((r) => r.id))
}

const gddOpen = ref(false)
const gddText = ref('')
const proposing = ref(false)
async function propose(): Promise<void> {
  if (!gddText.value.trim() || proposing.value) return
  proposing.value = true
  try {
    const out = await api().manifestFromGdd(props.projectId, gddText.value, GDD_LIMIT)
    if (!out.rows.length) {
      toasts.push('Claude không đề xuất được asset nào từ GDD này', 'info')
      return
    }
    rows.value.push(...out.rows)
    selected.value = new Set(out.rows.map((r) => r.id))
    pending = true
    await flush()
    gddOpen.value = false
    const skipped = out.dropped.length ? ` · bỏ ${out.dropped.length} dòng không khớp quy trình` : ''
    toasts.push(`Đã thêm ${out.rows.length} asset từ GDD (đã chọn sẵn)${skipped}`, 'success', 4000)
  } catch (e) {
    toasts.error(e, 'Không đọc được GDD')
  } finally {
    proposing.value = false
  }
}

const runnable = computed(() => rows.value.filter((r) => selected.value.has(r.id)))
const running = ref(false)
async function runSelected(): Promise<void> {
  const chosen = runnable.value
  if (!chosen.length || running.value) return
  const noRecipe = chosen.find((r) => !recipeById(r.recipe_id))
  if (noRecipe) {
    toasts.push(`“${noRecipe.name || 'Dòng chưa đặt tên'}” chưa chọn quy trình hợp lệ`, 'error')
    return
  }
  const rerun = chosen.filter((r) => ['running', 'review', 'approved'].includes(statusOf(r))).length
  if (rerun && !window.confirm(`${rerun} asset đã có lượt chạy. Chạy lại cả những asset này?`)) return
  running.value = true
  try {
    pending = true
    await flush()
    const v = await api().runManifest(props.projectId, chosen.map((r) => r.id))
    rows.value = v.rows.map(strip)
    serverStatus.value = new Map(v.rows.map((r) => [r.id, r.status]))
    selected.value = new Set()
    toasts.push(`Đã bắt đầu ${v.run_ids?.length ?? chosen.length} lượt chạy theo phong cách dự án`, 'success', 3000)
  } catch (e) {
    toasts.error(e, 'Không chạy được danh sách')
  } finally {
    running.value = false
  }
}

function slug(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/gi, 'd').replace(/[^\w-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'du-an'
}
async function exportAs(format: ManifestFormat): Promise<void> {
  try {
    await flush()
    const text = await api().exportManifest(props.projectId, format)
    const blob = new Blob([format === 'csv' ? '﻿' + text : text], { type: format === 'csv' ? 'text/csv' : 'application/x-yaml' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${slug(props.projectName)}-assets.${format === 'csv' ? 'csv' : 'yaml'}`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  } catch (e) {
    toasts.error(e, 'Không xuất được danh sách')
  }
}

const fileInput = ref<HTMLInputElement | null>(null)
async function importFile(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const format: ManifestFormat = /\.ya?ml$/i.test(file.name) ? 'yaml' : 'csv'
  const append = !rows.value.length || !window.confirm('Thay toàn bộ danh sách bằng tệp này? (Huỷ = thêm vào cuối)')
  try {
    await flush()
    const v = await api().importManifest(props.projectId, format, await file.text(), append)
    rows.value = v.rows.map(strip)
    serverStatus.value = new Map(v.rows.map((r) => [r.id, r.status]))
    toasts.push(`Đã nhập ${v.added?.length ?? 0} dòng`, 'success', 2500)
  } catch (e) {
    toasts.error(e, 'Không nhập được tệp')
  }
}

function numberOrEmpty(v: string): number | '' {
  return v.trim() === '' || !Number.isFinite(Number(v)) ? '' : Number(v)
}
function boolValue(raw: string): boolean | undefined {
  return raw === '' ? undefined : raw === 'true'
}
function optionValue(spec: RecipeInputSpec, raw: string): unknown {
  return spec.options?.find((o) => String(o) === raw) ?? raw
}
</script>

<template>
  <section class="manifest">
    <div class="bar">
      <div class="chips" aria-label="Tình trạng danh sách">
        <span v-for="s in STATUS_ORDER" :key="s" class="chip" :class="`m-${s}`" :data-zero="!counts[s]">{{ STATUS_LABEL[s] }} <b class="num">{{ counts[s] ?? 0 }}</b></span>
      </div>
      <span class="caption save-st" role="status">
        <template v-if="saving"><LoaderCircle class="spin" :size="12" /> Đang lưu…</template>
        <template v-else-if="saveError">Chưa lưu được</template>
      </span>
      <div class="actions">
        <button type="button" class="btn btn-plain" :aria-expanded="gddOpen" @click="gddOpen = !gddOpen"><Sparkles /> Từ GDD</button>
        <button type="button" class="btn btn-plain" @click="fileInput?.click()"><Upload /> Nhập</button>
        <input ref="fileInput" type="file" accept=".csv,.yaml,.yml,text/csv" hidden @change="importFile" />
        <button type="button" class="btn btn-plain" :disabled="!rows.length" title="Xuất CSV (mở được bằng Excel / Google Sheets)" @click="exportAs('csv')"><Download /> CSV</button>
        <button type="button" class="btn btn-plain" :disabled="!rows.length" @click="exportAs('yaml')"><Download /> YAML</button>
        <button type="button" class="btn btn-primary" :disabled="!runnable.length || running" @click="runSelected">
          <LoaderCircle v-if="running" class="spin" /> <Play v-else />
          Chạy <span class="num">{{ runnable.length || '' }}</span>
        </button>
      </div>
    </div>

    <Transition name="fold">
      <div v-if="gddOpen" class="gdd card">
        <label for="gdd-text" class="gdd-label">Dán GDD (tài liệu thiết kế game)</label>
        <textarea id="gdd-text" v-model="gddText" class="field" rows="8" maxlength="60000" placeholder="Thể loại, nhân vật, kẻ địch, vật phẩm, màn chơi, âm thanh…" :disabled="proposing" />
        <div class="gdd-foot">
          <span class="caption">Claude đề xuất tối đa {{ GDD_LIMIT }} asset, mỗi asset một quy trình có sẵn; prompt viết tiếng Anh, phong cách dự án tự áp khi chạy.</span>
          <button type="button" class="btn btn-plain" :disabled="proposing" @click="gddOpen = false">Đóng</button>
          <button type="button" class="btn btn-primary" :disabled="!gddText.trim() || proposing" @click="propose">
            <LoaderCircle v-if="proposing" class="spin" /> <Sparkles v-else />
            {{ proposing ? 'Claude đang đọc…' : 'Đề xuất danh sách' }}
          </button>
        </div>
      </div>
    </Transition>

    <p v-if="loading && !rows.length" class="caption state" role="status"><LoaderCircle class="spin" :size="14" /> Đang tải danh sách…</p>
    <EmptyState v-else-if="loadError" :icon="FileText" title="Không tải được danh sách asset" :text="loadError">
      <button class="btn" @click="load"><RotateCw /> Thử lại</button>
    </EmptyState>
    <EmptyState v-else-if="!rows.length" :icon="FileText" title="Chưa có danh sách asset" text="Dán GDD để Claude lập danh sách, nhập CSV/YAML, hoặc thêm từng dòng.">
      <button class="btn btn-primary" @click="gddOpen = true"><Sparkles /> Từ GDD</button>
      <button class="btn" @click="addRow"><Plus /> Thêm dòng</button>
    </EmptyState>

    <div v-else class="list card">
      <div class="head">
        <input type="checkbox" :checked="allSelected" aria-label="Chọn tất cả" @change="toggleAll" />
        <span class="caption">{{ rows.length }} asset · chọn rồi bấm Chạy, mỗi asset chạy theo quy trình riêng</span>
      </div>
      <div v-auto-animate>
        <div v-for="(row, i) in rows" :key="row.id" class="row" :class="{ sel: selected.has(row.id) }">
          <div class="line">
            <input type="checkbox" :checked="selected.has(row.id)" :aria-label="`Chọn ${row.name || `dòng ${i + 1}`}`" @change="selected = toggle(selected, row.id)" />
            <input v-model="row.name" class="field name" maxlength="200" placeholder="Tên asset" :aria-label="`Tên asset ${i + 1}`" @input="scheduleSave" />
            <select class="field recipe" :value="row.recipe_id" :aria-label="`Quy trình ${i + 1}`" :aria-invalid="!recipeById(row.recipe_id)" @change="changeRecipe(row, ($event.target as HTMLSelectElement).value)">
              <option v-if="!recipeById(row.recipe_id)" :value="row.recipe_id">{{ row.recipe_id || 'Chọn quy trình' }} (không có)</option>
              <option v-for="r in recipes.list" :key="r.id" :value="r.id">{{ r.name }}</option>
            </select>
            <component :is="row.run_id ? 'RouterLink' : 'span'" :to="row.run_id ? `/runs/${row.run_id}` : undefined" class="st" :class="`m-${statusOf(row)}`" :title="row.run_id ? 'Mở lượt chạy' : undefined">
              <span class="dot" />{{ STATUS_LABEL[statusOf(row)] }}
            </component>
            <button type="button" class="btn btn-plain btn-icon" :aria-label="`Bỏ dòng ${i + 1}`" @click="removeRow(row)"><X /></button>
          </div>
          <div class="body">
            <textarea
              v-if="primaryInput(recipeById(row.recipe_id))"
              class="field prompt"
              rows="2"
              spellcheck="false"
              :placeholder="recipeById(row.recipe_id)?.inputs[primaryInput(recipeById(row.recipe_id))!]?.label ?? 'Prompt'"
              :value="String(row.inputs[primaryInput(recipeById(row.recipe_id))!] ?? '')"
              :aria-label="`Prompt ${i + 1}`"
              @input="setInput(row, primaryInput(recipeById(row.recipe_id))!, ($event.target as HTMLTextAreaElement).value)"
            />
            <input v-model="row.notes" class="field notes" maxlength="4000" placeholder="Ghi chú" :aria-label="`Ghi chú ${i + 1}`" @input="scheduleSave" />
          </div>
          <div class="foot">
            <button v-if="extraInputs(recipeById(row.recipe_id)).length" type="button" class="link caption" :aria-expanded="expanded.has(row.id)" @click="expanded = toggle(expanded, row.id)">
              {{ expanded.has(row.id) ? 'Ẩn đầu vào khác' : `Đầu vào khác (${Object.keys(row.inputs).filter((k) => k !== primaryInput(recipeById(row.recipe_id))).length} đã đặt)` }}
            </button>
            <span v-if="fileInputs(recipeById(row.recipe_id)).length" class="caption warn">Cần tệp: {{ fileInputs(recipeById(row.recipe_id)).join(', ') }} — chạy trong Tạo mới hoặc đặt ảnh tham chiếu ở Phong cách</span>
          </div>
          <div v-if="expanded.has(row.id)" class="extra">
            <label v-for="[k, spec] in extraInputs(recipeById(row.recipe_id))" :key="k" class="xi">
              <span class="caption">{{ spec.label ?? k }}</span>
              <select v-if="spec.options?.length" class="field" :value="row.inputs[k] === undefined ? '' : String(row.inputs[k])" @change="setInput(row, k, optionValue(spec, ($event.target as HTMLSelectElement).value))">
                <option value="">Mặc định ({{ spec.default ?? '—' }})</option>
                <option v-for="o in spec.options" :key="String(o)" :value="String(o)">{{ o }}</option>
              </select>
              <select v-else-if="spec.type === 'bool'" class="field" :value="row.inputs[k] === undefined ? '' : String(row.inputs[k])" @change="setInput(row, k, boolValue(($event.target as HTMLSelectElement).value))">
                <option value="">Mặc định ({{ spec.default ?? '—' }})</option>
                <option value="true">Bật</option>
                <option value="false">Tắt</option>
              </select>
              <input v-else-if="spec.type === 'int' || spec.type === 'float'" class="field num" type="number" :min="spec.min" :max="spec.max" :step="spec.step ?? (spec.type === 'int' ? 1 : 'any')" :placeholder="spec.default === undefined ? '' : String(spec.default)" :value="row.inputs[k] ?? ''" @input="setInput(row, k, numberOrEmpty(($event.target as HTMLInputElement).value))" />
              <input v-else class="field" :placeholder="spec.default === undefined ? '' : String(spec.default)" :value="row.inputs[k] ?? ''" @input="setInput(row, k, ($event.target as HTMLInputElement).value)" />
            </label>
          </div>
        </div>
      </div>
      <button type="button" class="btn btn-plain small add" @click="addRow"><Plus :size="13" /> Thêm dòng</button>
    </div>
  </section>
</template>

<style scoped>
.manifest {
  display: grid;
  gap: 12px;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  color: var(--text-2);
  background: var(--bg-active);
  transition: opacity var(--t-base);
}
.chip[data-zero='true'] {
  opacity: 0.45;
}
.save-st {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 70px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-left: auto;
}
.gdd {
  display: grid;
  gap: 8px;
  padding: 14px 18px;
}
.gdd-foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gdd-foot .caption {
  flex: 1;
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.list {
  padding: 4px 0 8px;
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
}
.row {
  display: grid;
  gap: 6px;
  padding: 10px 14px;
  border-top: 0.5px solid var(--separator);
  transition: background var(--t-fast);
}
.row.sel {
  background: var(--accent-soft);
}
.line {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(0, 220px) auto auto;
  align-items: center;
  gap: 8px;
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 8px;
  padding-left: 24px;
}
.prompt {
  resize: vertical;
}
.foot {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding-left: 24px;
}
.foot:empty {
  display: none;
}
.link {
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
}
.warn {
  color: var(--st-waiting);
}
.extra {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 8px;
  padding-left: 24px;
}
.xi {
  display: grid;
  gap: 3px;
}
.st {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 9px 0 8px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  white-space: nowrap;
  color: var(--text-2);
  background: var(--bg-active);
  text-decoration: none;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.m-running {
  color: var(--st-running);
  background: var(--st-running-soft);
}
.m-running .dot {
  animation: soft-pulse 1.6s ease-in-out infinite;
}
.m-review {
  color: var(--st-human);
  background: var(--st-human-soft);
}
.m-approved {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.m-failed {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.add {
  margin: 6px 0 0 10px;
}
.small {
  height: 24px;
  padding: 0 8px;
  font-size: var(--fs-callout);
}
.gdd-label {
  font-weight: 500;
  color: var(--text-2);
}
.spin {
  animation: spin 1s linear infinite;
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
@media (prefers-reduced-motion: reduce) {
  .fold-enter-active,
  .fold-leave-active,
  .m-running .dot {
    animation: none;
    transition: none;
  }
}
@media (max-width: 720px) {
  .line {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }
  .line .recipe {
    grid-column: 2 / -1;
  }
  .body {
    grid-template-columns: 1fr;
  }
}
</style>
