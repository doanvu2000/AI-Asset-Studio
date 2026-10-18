<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Copy, FolderCog, Lock, LoaderCircle, Plus, Save, ScrollText, Trash2 } from '@lucide/vue'
import type { Compass, ExportPreset } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import DiagnosticsPanel from '@/components/DiagnosticsPanel.vue'
import ToolPathsPanel from '@/components/ToolPathsPanel.vue'
import { usePresets } from '@/stores/catalog'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const presets = usePresets()
const copy = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
const conn = useConnection()
const toasts = useToasts()

watch(
  () => conn.transport,
  (t) => t && void presets.load(true),
  { immediate: true }
)

// Screen bearing of each facing, for the arrow glyph (0° = up, clockwise).
const BEARING: Record<Compass, number> = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 }
const ANGLE: Record<Compass, number> = { S: 0, SW: 45, W: 90, NW: 135, N: 180, NE: 225, E: 270, SE: 315 }
const COMPASS_VI: Record<Compass, string> = { N: 'Bắc', NE: 'Đông Bắc', E: 'Đông', SE: 'Đông Nam', S: 'Nam', SW: 'Tây Nam', W: 'Tây', NW: 'Tây Bắc' }

const selectedId = ref<string>('studio')
const pane = ref<'tools' | 'diag' | null>(null)
const draft = ref<ExportPreset | null>(null)
const isNew = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const selected = computed(() => presets.list.find((p) => p.id === selectedId.value))
watch(
  selected,
  (p) => {
    if (p && !isNew.value) draft.value = copy(p)
  },
  { immediate: true }
)
const readonly = computed(() => !!draft.value?.builtin)
const dirty = computed(() => isNew.value || (!!draft.value && !!selected.value && JSON.stringify(draft.value) !== JSON.stringify(selected.value)))

function pick(id: string): void {
  pane.value = null
  isNew.value = false
  error.value = null
  selectedId.value = id
  draft.value = copy(presets.list.find((p) => p.id === id) ?? null)
}

function duplicate(): void {
  if (!draft.value) return
  const base = draft.value.id.replace(/-copy\d*$/, '')
  let id = `${base}-copy`
  for (let i = 2; presets.list.some((p) => p.id === id); i++) id = `${base}-copy${i}`
  draft.value = { ...copy(draft.value), id, name: `${draft.value.name} (bản sao)`, builtin: false }
  isNew.value = true
  pane.value = null
  error.value = null
}

function blank(): void {
  draft.value = {
    id: 'preset-moi',
    name: 'Preset mới',
    description: '',
    dir_order: ['S', 'SW', 'W', 'NW', 'N', 'NE', 'E', 'SE'],
    dir_labels: {},
    file_pattern: '{asset}_{dir}',
    layout: 'row',
    cols: 4,
    padding: 0,
    pivot_origin: 'top_left',
    builtin: false
  }
  isNew.value = true
  pane.value = null
  error.value = null
}

function move(i: number, d: -1 | 1): void {
  const order = draft.value?.dir_order
  const j = i + d
  if (!order || j < 0 || j >= order.length) return
  ;[order[i], order[j]] = [order[j]!, order[i]!]
}

function setLabel(c: Compass, v: string): void {
  if (!draft.value) return
  const labels = { ...draft.value.dir_labels }
  if (v.trim()) labels[c] = v.trim()
  else delete labels[c]
  draft.value.dir_labels = labels
}

function frameName(c: Compass, index: number): string {
  const p = draft.value
  if (!p) return ''
  return p.file_pattern.replace(/\{(\w+)(?::(0?)(\d+)d)?\}/g, (m, f: string, zero: string, width: string) => {
    const v = f === 'asset' ? 'hero' : f === 'dir' ? (p.dir_labels[c] ?? c) : f === 'angle' ? ANGLE[c] : f === 'index' ? index : m
    return width ? String(v).padStart(Number(width), zero ? '0' : ' ') : String(v)
  })
}

const cols = computed(() => {
  const p = draft.value
  if (!p) return 8
  return p.layout === 'row' ? 8 : p.layout === 'column' ? 1 : Math.min(p.cols, 8)
})

async function save(): Promise<void> {
  if (!draft.value || saving.value) return
  saving.value = true
  error.value = null
  try {
    const saved = await presets.save(copy(draft.value))
    isNew.value = false
    selectedId.value = saved.id
    draft.value = copy(saved)
    toasts.push('Đã lưu preset', 'success', 2200)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

const confirmDel = ref(false)
let delTimer: number | undefined
async function remove(): Promise<void> {
  if (!draft.value) return
  if (isNew.value) return pick('studio')
  if (!confirmDel.value) {
    confirmDel.value = true
    delTimer = window.setTimeout(() => (confirmDel.value = false), 3000)
    return
  }
  window.clearTimeout(delTimer)
  confirmDel.value = false
  try {
    await presets.remove(draft.value.id)
    toasts.push('Đã xoá preset', 'info')
    pick('studio')
  } catch (e) {
    toasts.error(e, 'Không xoá được')
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Cài đặt" subtitle="Đường dẫn công cụ, nhật ký chẩn đoán và preset xuất sprite sheet" />
    <div class="split">
      <aside class="list" aria-label="Cài đặt">
        <p class="section-label pad">Hệ thống</p>
        <button class="item" :class="{ on: pane === 'tools' }" @click="pane = 'tools'">
          <span class="i-name">Đường dẫn công cụ</span>
          <FolderCog :size="13" class="lock" aria-hidden="true" />
          <span class="i-sub caption">ComfyUI · Blender · Codex</span>
        </button>
        <button class="item" :class="{ on: pane === 'diag' }" @click="pane = 'diag'">
          <span class="i-name">Nhật ký chẩn đoán</span>
          <ScrollText :size="13" class="lock" aria-hidden="true" />
          <span class="i-sub caption">Copy · xuất log khi báo lỗi</span>
        </button>
        <p class="section-label pad sep">Preset xuất</p>
        <button v-for="p in presets.list" :key="p.id" class="item" :class="{ on: !pane && !isNew && selectedId === p.id }" @click="pick(p.id)">
          <span class="i-name">{{ p.name }}</span>
          <Lock v-if="p.builtin" :size="12" class="lock" aria-label="Có sẵn" />
          <span class="i-sub caption">{{ p.dir_order.slice(0, 4).join(' · ') }} …</span>
        </button>
        <div v-if="isNew && draft" class="item new" :class="{ on: !pane }">
          <span class="i-name">{{ draft.name }}</span>
          <span class="i-sub caption">Chưa lưu</span>
        </div>
        <button class="btn btn-plain add" @click="blank"><Plus /> Preset mới</button>
      </aside>

      <section v-if="pane === 'tools'" class="scroll pane">
        <ToolPathsPanel />
      </section>
      <section v-else-if="pane === 'diag'" class="scroll pane">
        <DiagnosticsPanel />
      </section>
      <section v-else-if="draft" class="scroll pane">
        <div class="form">
          <header class="f-head">
            <div>
              <h2 class="large-title">{{ draft.name }}</h2>
              <p class="secondary">{{ readonly ? 'Preset có sẵn — nhân bản để chỉnh.' : draft.description || 'Preset của bạn' }}</p>
            </div>
            <div class="actions">
              <button class="btn" @click="duplicate"><Copy /> Nhân bản</button>
              <button v-if="!readonly" class="btn" :class="{ 'btn-danger': confirmDel }" @click="remove"><Trash2 /> {{ confirmDel ? 'Bấm lần nữa' : isNew ? 'Bỏ' : 'Xoá' }}</button>
              <button v-if="!readonly" class="btn btn-primary" :disabled="!dirty || saving" @click="save">
                <LoaderCircle v-if="saving" class="spin" />
                <Save v-else />
                Lưu
              </button>
            </div>
          </header>
          <p v-if="error" class="err" role="alert">{{ error }}</p>

          <fieldset class="card fields" :disabled="readonly">
            <div class="f">
              <label for="p-id" class="lbl">Mã</label>
              <input id="p-id" v-model="draft.id" class="field mono" :readonly="!isNew" />
            </div>
            <div class="f">
              <label for="p-name" class="lbl">Tên</label>
              <input id="p-name" v-model="draft.name" class="field" />
            </div>
            <div class="f">
              <label for="p-desc" class="lbl">Mô tả</label>
              <input id="p-desc" v-model="draft.description" class="field" />
            </div>
            <div class="f">
              <label for="p-pattern" class="lbl">Tên khung</label>
              <div>
                <input id="p-pattern" v-model="draft.file_pattern" class="field mono" />
                <p class="caption help">Dùng <span class="mono">{asset}</span>, <span class="mono">{dir}</span>, <span class="mono">{angle:03d}</span>, <span class="mono">{index}</span>. Ví dụ: <span class="mono">{{ frameName(draft.dir_order[0]!, 0) }}</span></p>
              </div>
            </div>
            <div class="f">
              <span class="lbl">Bố cục sheet</span>
              <div class="inline">
                <div class="seg" role="radiogroup" aria-label="Bố cục">
                  <button v-for="l in [['row', '1 hàng'], ['column', '1 cột'], ['grid', 'Lưới']] as const" :key="l[0]" type="button" role="radio" :aria-checked="draft.layout === l[0]" :class="{ on: draft.layout === l[0] }" @click="draft.layout = l[0]">{{ l[1] }}</button>
                </div>
                <label v-if="draft.layout === 'grid'" class="mini">Số cột <input v-model.number="draft.cols" class="field num tiny" type="number" min="1" max="8" /></label>
                <label class="mini">Khoảng cách <input v-model.number="draft.padding" class="field num tiny" type="number" min="0" max="64" /> px</label>
              </div>
            </div>
            <div class="f">
              <span class="lbl">Gốc pivot</span>
              <div class="seg" role="radiogroup" aria-label="Gốc pivot">
                <button type="button" role="radio" :aria-checked="draft.pivot_origin === 'top_left'" :class="{ on: draft.pivot_origin === 'top_left' }" @click="draft.pivot_origin = 'top_left'">Trên-trái (Godot)</button>
                <button type="button" role="radio" :aria-checked="draft.pivot_origin === 'bottom_left'" :class="{ on: draft.pivot_origin === 'bottom_left' }" @click="draft.pivot_origin = 'bottom_left'">Dưới-trái (Unity)</button>
              </div>
            </div>
          </fieldset>

          <div class="two">
            <section class="grp">
              <h3 class="section-label">Xem trước sheet</h3>
              <div class="sheet card checker" :style="{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: `${Math.min(draft.padding, 8)}px`, '--n': cols }">
                <div v-for="(c, i) in draft.dir_order" :key="c" class="cell">
                  <ArrowUp :size="22" class="c-arrow" :style="{ transform: `rotate(${BEARING[c]}deg)` }" />
                  <span class="c-name mono">{{ frameName(c, i) }}</span>
                </div>
              </div>
            </section>
            <section class="grp">
              <h3 class="section-label">Thứ tự hướng & tên</h3>
              <ol v-auto-animate class="dirs card">
                <li v-for="(c, i) in draft.dir_order" :key="c" class="dir">
                  <span class="d-idx num">{{ i }}</span>
                  <ArrowUp :size="15" class="d-arrow" :style="{ transform: `rotate(${BEARING[c]}deg)` }" />
                  <span class="d-name"><strong>{{ c }}</strong> <span class="caption">{{ COMPASS_VI[c] }} · {{ ANGLE[c] }}°</span></span>
                  <input class="field mono d-label" :placeholder="c" :value="draft.dir_labels[c] ?? ''" :disabled="readonly" :aria-label="`Nhãn hướng ${c}`" @change="setLabel(c, ($event.target as HTMLInputElement).value)" />
                  <span v-if="!readonly" class="d-move">
                    <button type="button" class="btn btn-plain btn-icon" :disabled="i === 0" :aria-label="`Đưa ${c} lên`" @click="move(i, -1)"><ArrowUp /></button>
                    <button type="button" class="btn btn-plain btn-icon" :disabled="i === draft.dir_order.length - 1" :aria-label="`Đưa ${c} xuống`" @click="move(i, 1)"><ArrowDown /></button>
                  </span>
                </li>
              </ol>
              <p class="caption">Góc = model xoay bao nhiêu so với lúc nhìn thẳng vào camera (0° = hướng xuống màn hình).</p>
            </section>

          </div>
        </div>
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
.list {
  width: 250px;
  flex: none;
  overflow: auto;
  padding: 16px 10px;
  border-right: 0.5px solid var(--separator);
  display: grid;
  align-content: start;
  gap: 3px;
}
.pad {
  padding: 0 8px 6px;
}
.sep {
  margin-top: 14px;
}
.item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 1px 6px;
  text-align: left;
  padding: 8px 12px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.item:hover {
  background: var(--bg-hover);
}
.item.on {
  background: var(--bg-selected);
}
.i-name {
  font-weight: 600;
}
.lock {
  color: var(--text-3);
  align-self: center;
}
.i-sub {
  grid-column: 1 / -1;
  font-family: var(--mono);
}
.add {
  justify-self: start;
  margin-top: 6px;
}
.pane {
  flex: 1;
}
.form {
  max-width: 860px;
  margin: 0 auto;
  display: grid;
  gap: 18px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.f-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.actions {
  display: flex;
  gap: 8px;
}
.err {
  color: var(--st-fail);
  font-size: var(--fs-callout);
}
.fields {
  border: 0;
  margin: 0;
  padding: 4px 18px;
  display: grid;
}
.fields:disabled {
  opacity: 0.85;
}
.f {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 16px;
  padding: 11px 0;
  align-items: center;
}
.f + .f {
  border-top: 0.5px solid var(--separator);
}
.lbl {
  font-weight: 500;
  color: var(--text-2);
}
.mono {
  font-family: var(--mono);
}
.field.mono {
  font-size: 12.5px;
}
.help {
  margin-top: 5px;
}
.inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
}
.mini {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
  font-size: var(--fs-callout);
}
.tiny {
  width: 64px;
  min-height: 28px;
  padding: 3px 8px;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  justify-self: start;
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
.two {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  align-items: start;
  padding-bottom: 24px;
}
.grp {
  display: grid;
  gap: 8px;
}
.dirs {
  list-style: none;
  margin: 0;
  padding: 4px 8px;
}
.dir {
  display: grid;
  grid-template-columns: 20px 20px 1fr 110px auto;
  align-items: center;
  gap: 8px;
  min-height: 40px;
}
.dir + .dir {
  border-top: 0.5px solid var(--separator);
}
.d-idx {
  color: var(--text-3);
  font-size: var(--fs-caption);
  text-align: right;
}
.d-arrow,
.c-arrow {
  color: var(--accent);
  transition: transform var(--t-slow) var(--ease-spring);
}
.d-label {
  min-height: 28px;
  padding: 3px 8px;
}
.d-move {
  display: flex;
}
.d-move .btn {
  width: 26px;
  height: 26px;
}
.sheet {
  display: grid;
  max-width: 100%;
  justify-self: start;
  width: min(100%, calc(var(--n) * 96px + 20px));
  padding: 10px;
  border-radius: var(--r-md);
}
.cell {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 4px;
  aspect-ratio: 1;
  min-width: 0;
  border-radius: 6px;
  background: color-mix(in srgb, var(--bg-elevated) 80%, transparent);
  box-shadow: var(--hairline);
}
.c-name {
  max-width: 100%;
  padding: 0 3px;
  font-size: 9.5px;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
