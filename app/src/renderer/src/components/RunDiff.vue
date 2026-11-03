<script setup lang="ts">
import { computed, ref, useTemplateRef, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { CircleAlert, Columns2, LoaderCircle, RefreshCw, TriangleAlert, X } from '@lucide/vue'
import type { BlobRef, RunDetail, StepRow } from '@/api/types'
import { RUN_ACTIVE, STEP_DONE_OK } from '@/api/types'
import { api } from '@/stores/connection'
import { useRecipes } from '@/stores/recipes'
import { useFocusTrap } from '@/composables/focusTrap'
import { absoluteTime, humanDuration, RUN_LABEL, stepName } from '@/composables/format'
import BlobThumb from './BlobThumb.vue'
import CompareView, { type CompareItem } from './CompareView.vue'
import { isImage, outputBlobs } from './viewers/detect'

const props = defineProps<{ a: string | null; b: string | null }>()
const emit = defineEmits<{ close: [] }>()

type Cell = { kind: 'text'; text: string; empty?: boolean } | { kind: 'blobs'; blobs: BlobRef[] }
interface Row {
  key: string
  label: string
  a: Cell
  b: Cell
  differs: boolean
}
interface Section {
  id: string
  title: string
  rows: Row[]
}
interface Side {
  detail: RunDetail
  hero: { blobs: BlobRef[]; lead: BlobRef } | null
}

const recipes = useRecipes()
const open = computed(() => !!props.a && !!props.b)
const sides = ref<[Side, Side] | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const showAll = ref(false)
const compareOpen = ref(false)
const root = useTemplateRef<HTMLElement>('root')
const closeBtn = useTemplateRef<HTMLButtonElement>('closeBtn')
const trap = useFocusTrap(root, open, closeBtn)

function heroOf(steps: StepRow[]): Side['hero'] {
  for (const s of [...steps].reverse()) {
    if (!STEP_DONE_OK.has(s.status)) continue
    const blobs = outputBlobs(s.outputs)
    const lead = blobs.find(isImage)
    if (lead) return { blobs, lead }
  }
  return null
}

let seq = 0
async function load(): Promise<void> {
  if (!props.a || !props.b) return
  const mine = ++seq
  loading.value = true
  error.value = null
  try {
    const [da, db] = await Promise.all([api().getRun(props.a), api().getRun(props.b)])
    if (mine !== seq) return
    sides.value = [
      { detail: da, hero: heroOf(da.steps) },
      { detail: db, hero: heroOf(db.steps) }
    ]
  } catch (e) {
    if (mine !== seq) return
    sides.value = null
    error.value = (e as Error).message || 'Không tải được lượt chạy'
  } finally {
    if (mine === seq) loading.value = false
  }
}
watch(
  () => [props.a, props.b] as const,
  ([a, b]) => {
    compareOpen.value = false
    showAll.value = false
    if (a && b) void load()
    else {
      seq++
      sides.value = null
      error.value = null
      loading.value = false
    }
  },
  { immediate: true }
)

const isBlob = (v: unknown): v is BlobRef =>
  !!v && typeof v === 'object' && typeof (v as BlobRef).hash === 'string' && typeof (v as BlobRef).mime === 'string'

function cellOf(v: unknown): Cell {
  if (v === undefined || v === null || v === '') return { kind: 'text', text: '—', empty: true }
  if (isBlob(v)) return { kind: 'blobs', blobs: [v] }
  if (Array.isArray(v) && v.length && v.every(isBlob)) return { kind: 'blobs', blobs: v }
  if (typeof v === 'string') return { kind: 'text', text: v }
  if (typeof v === 'number' || typeof v === 'boolean') return { kind: 'text', text: String(v) }
  return { kind: 'text', text: JSON.stringify(v, null, 1) }
}
const sig = (c: Cell): string => (c.kind === 'blobs' ? `b:${c.blobs.map((x) => x.hash).join(',')}` : `t:${c.text}`)
const text = (t: string, empty = false): Cell => ({ kind: 'text', text: t, empty })

function mk(key: string, label: string, a: Cell, b: Cell): Row {
  return { key, label, a, b, differs: sig(a) !== sig(b) }
}

function union(a: Record<string, unknown>, b: Record<string, unknown>): string[] {
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => k !== '$meta')
}

function duration(d: RunDetail): Cell {
  const r = d.run
  if (RUN_ACTIVE.has(r.status)) return text('chưa kết thúc', true)
  return r.ended_at ? text(humanDuration(r.ended_at - r.created_at)) : text('—', true)
}

function cost(d: RunDetail): Cell {
  const vals = d.steps.map((s) => s.meta.cost_usd).filter((v): v is number => typeof v === 'number')
  return vals.length ? text(`$${vals.reduce((x, y) => x + y, 0).toFixed(4)}`) : text('—', true)
}

function seedCell(s: StepRow | undefined): Cell {
  if (!s) return text('không có bước này', true)
  return typeof s.meta.seed_used === 'number' ? text(String(s.meta.seed_used)) : text('không dùng seed', true)
}

const sections = computed<Section[]>(() => {
  const s = sides.value
  if (!s) return []
  const [A, B] = s
  const ra = A.detail.run
  const rb = B.detail.run
  const name = (id: string): string => recipes.byId(id)?.name ?? id
  const overview: Row[] = [
    mk('recipe', 'Quy trình', text(name(ra.recipe_id)), text(name(rb.recipe_id))),
    mk('status', 'Trạng thái', text(RUN_LABEL[ra.status]), text(RUN_LABEL[rb.status])),
    mk('dur', 'Thời gian chạy', duration(A.detail), duration(B.detail)),
    mk('cost', 'Chi phí', cost(A.detail), cost(B.detail))
  ]
  if (ra.error || rb.error) overview.push(mk('error', 'Lỗi', cellOf(ra.error), cellOf(rb.error)))

  const inputs = union(ra.inputs, rb.inputs).map((k) => mk(`in-${k}`, k, cellOf(ra.inputs[k]), cellOf(rb.inputs[k])))
  const params = union(ra.params, rb.params).map((k) => mk(`p-${k}`, k, cellOf(ra.params[k]), cellOf(rb.params[k])))

  const byKey = (steps: StepRow[]): Map<string, StepRow> => new Map(steps.map((x) => [x.key, x]))
  const ma = byKey(A.detail.steps)
  const mb = byKey(B.detail.steps)
  const keys = [...new Set([...A.detail.steps.map((x) => x.key), ...B.detail.steps.map((x) => x.key)])]
  const seeds = keys.map((k) => mk(`s-${k}`, stepName(ma.get(k) ?? mb.get(k)!), seedCell(ma.get(k)), seedCell(mb.get(k))))

  return [
    { id: 'overview', title: 'Tổng quan', rows: overview },
    { id: 'inputs', title: 'Đầu vào', rows: inputs },
    { id: 'params', title: 'Tham số', rows: params },
    { id: 'seeds', title: 'Seed từng bước', rows: seeds }
  ]
})
const visible = computed(() =>
  sections.value.map((sec) => ({ ...sec, rows: showAll.value ? sec.rows : sec.rows.filter((r) => r.differs) })).filter((sec) => sec.rows.length)
)
const diffCount = computed(() => sections.value.reduce((n, sec) => n + sec.rows.filter((r) => r.differs).length, 0))

const sameRecipe = computed(() => !sides.value || sides.value[0].detail.run.recipe_id === sides.value[1].detail.run.recipe_id)
const activeLabels = computed(() => (sides.value ? (['A', 'B'] as const).filter((_, i) => RUN_ACTIVE.has(sides.value![i]!.detail.run.status)) : []))

function titleOf(d: RunDetail): string {
  return d.run.title || recipes.byId(d.run.recipe_id)?.name || d.run.recipe_id
}

const compareItems = computed<CompareItem[] | null>(() => {
  const s = sides.value
  if (!compareOpen.value || !s || !s[0].hero || !s[1].hero) return null
  return s.map((x, i) => ({
    key: x.detail.run.id,
    name: `${i ? 'B' : 'A'} · ${titleOf(x.detail)}`,
    meta: absoluteTime(x.detail.run.created_at),
    lead: x.hero!.lead,
    siblings: x.hero!.blobs
  }))
})
const canCompare = computed(() => !!sides.value?.[0].hero && !!sides.value[1].hero)

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!open.value || compareOpen.value) return
    if (trap.onTab(e)) return
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      emit('close')
    }
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="rd">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-label="So sánh 2 lượt chạy" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <h2 class="headline">So sánh 2 lượt chạy</h2>
            <span v-if="sides" class="caption num">{{ diffCount }} mục khác nhau</span>
            <span class="grow" />
            <button
              type="button"
              class="btn btn-plain"
              role="switch"
              :aria-checked="showAll"
              title="Hiện cả các dòng giống nhau"
              @click="showAll = !showAll"
            >
              <span class="sw" :class="{ on: showAll }" aria-hidden="true"><i /></span> Hiện tất cả
            </button>
            <button type="button" class="btn btn-plain btn-icon" aria-label="Tải lại" title="Tải lại (lượt đang chạy sẽ đổi)" :disabled="loading" @click="load">
              <RefreshCw :class="{ spin: loading }" />
            </button>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div v-if="loading && !sides" class="state caption"><LoaderCircle class="spin" /> Đang tải hai lượt chạy…</div>
          <div v-else-if="error" class="state" role="alert">
            <CircleAlert :size="18" />
            <span>{{ error }}</span>
            <button type="button" class="btn" @click="load">Thử lại</button>
          </div>
          <div v-else-if="sides" class="body">
            <div v-if="!sameRecipe" class="note warn" role="status">
              <TriangleAlert :size="16" />
              <span>Hai lượt dùng quy trình khác nhau, nên đầu vào, tham số và seed có thể không tương ứng từng dòng.</span>
            </div>
            <div v-if="activeLabels.length" class="note warn" role="status">
              <TriangleAlert :size="16" />
              <span>Lượt {{ activeLabels.join(' và ') }} chưa kết thúc (đang chạy hoặc chờ duyệt): số liệu chưa đầy đủ. Bấm Tải lại để cập nhật.</span>
            </div>

            <div class="grid heads">
              <span />
              <div v-for="(s, i) in sides" :key="s.detail.run.id" class="col-head">
                <span class="tag">{{ i ? 'B' : 'A' }}</span>
                <div class="ct">
                  <strong class="clip" :title="titleOf(s.detail)">{{ titleOf(s.detail) }}</strong>
                  <span class="caption clip">{{ absoluteTime(s.detail.run.created_at) }}</span>
                </div>
              </div>
            </div>

            <div class="grid result">
              <span class="lbl-cell">Kết quả</span>
              <div v-for="s in sides" :key="s.detail.run.id" class="cell">
                <div v-if="s.hero" class="hero-thumb checker"><BlobThumb :blob="s.hero.lead" :size="256" /></div>
                <span v-else class="empty">Chưa có ảnh kết quả</span>
              </div>
              <span />
              <div class="result-act">
                <button type="button" class="btn" :disabled="!canCompare" :title="canCompare ? 'Mở so sánh ảnh kết quả' : 'Cần cả hai lượt đều có ảnh kết quả'" @click="compareOpen = true">
                  <Columns2 /> So sánh ảnh kết quả
                </button>
              </div>
            </div>

            <p v-if="!visible.length" class="state caption">Hai lượt giống hệt nhau ở mọi mục. Bật “Hiện tất cả” để xem chi tiết.</p>
            <section v-for="sec in visible" :key="sec.id" class="sec" :aria-label="sec.title">
              <h3 class="section-label">{{ sec.title }}</h3>
              <div v-for="r in sec.rows" :key="r.key" class="grid line" :class="{ differs: r.differs }">
                <span class="lbl-cell clip" :title="r.label">{{ r.label }}</span>
                <div v-for="(c, i) in [r.a, r.b]" :key="i" class="cell">
                  <div v-if="c.kind === 'blobs'" class="blobs">
                    <span v-for="bl in c.blobs.slice(0, 4)" :key="bl.hash" class="mini checker"><BlobThumb :blob="bl" :size="96" /></span>
                    <span v-if="c.blobs.length > 4" class="caption num">+{{ c.blobs.length - 4 }}</span>
                  </div>
                  <span v-else class="val num selectable" :class="{ empty: c.empty }">{{ c.text }}</span>
                </div>
              </div>
            </section>
          </div>
          <footer class="foot caption">Esc đóng · Tab di chuyển</footer>
        </section>
      </div>
    </Transition>
  </Teleport>
  <CompareView :items="compareItems" @close="compareOpen = false" />
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 55;
  display: grid;
  place-items: center;
  padding: 32px 24px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.sheet {
  width: min(1040px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  border-radius: var(--r-xl);
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px 10px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.grow {
  flex: 1;
}
.body {
  overflow: auto;
  padding: var(--sp-4) var(--sp-5);
  display: grid;
  gap: var(--sp-3);
  align-content: start;
}
.state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 16px;
  color: var(--text-2);
}
.note {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 14px;
  border-radius: var(--r-md);
  background: var(--st-waiting-soft);
  box-shadow: var(--hairline);
}
.note svg {
  flex: none;
  color: var(--st-waiting);
}
.grid {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.heads {
  position: sticky;
  top: calc(-1 * var(--sp-4));
  z-index: 1;
  padding: 6px 0;
  background: var(--bg-elevated);
}
.col-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ct {
  display: grid;
  min-width: 0;
}
.tag {
  flex: none;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-soft);
}
.clip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lbl-cell {
  padding-top: 4px;
  color: var(--text-2);
  font-size: var(--fs-callout);
}
.result .cell {
  display: grid;
}
.hero-thumb {
  width: 100%;
  aspect-ratio: 1;
  max-height: 240px;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.hero-thumb :deep(.blob),
.mini :deep(.blob) {
  width: 100%;
  height: 100%;
}
.result-act {
  grid-column: 2 / 4;
}
.sec {
  display: grid;
  gap: 2px;
  margin-top: var(--sp-2);
}
.sec .section-label {
  margin: 0 0 4px;
}
.line {
  padding: 4px 0;
  border-top: 1px solid var(--hair-soft);
}
.cell {
  min-width: 0;
  padding: 3px 8px;
  border-radius: var(--r-sm);
}
.line.differs .cell {
  background: var(--accent-soft);
}
.val {
  white-space: pre-wrap;
  word-break: break-word;
}
.empty {
  color: var(--text-3);
}
.blobs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.mini {
  width: 44px;
  height: 44px;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: var(--hairline);
}
.foot {
  padding: 8px 18px;
  border-top: 0.5px solid var(--separator);
  color: var(--text-3);
}
.sw {
  position: relative;
  width: 28px;
  height: 16px;
  border-radius: 8px;
  background: var(--bg-active);
  transition: background-color var(--t-fast);
}
.sw i {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  transition: transform var(--t-fast) var(--ease-spring);
}
.sw.on {
  background: var(--accent);
}
.sw.on i {
  transform: translateX(12px);
}
.spin {
  animation: spin 1s linear infinite;
}
.rd-enter-active {
  transition: opacity 160ms var(--ease-out);
}
.rd-enter-active .sheet {
  transition: transform 220ms var(--ease-spring);
}
.rd-leave-active {
  transition: opacity 100ms var(--ease-out);
}
.rd-enter-from,
.rd-leave-to {
  opacity: 0;
}
.rd-enter-from .sheet {
  transform: translateY(8px) scale(0.98);
}
@media (prefers-reduced-motion: reduce) {
  .rd-enter-active,
  .rd-enter-active .sheet,
  .sw,
  .sw i {
    transition: none;
  }
}
</style>
