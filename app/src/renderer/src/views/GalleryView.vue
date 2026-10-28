<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useElementSize, useEventListener, useIntervalFn } from '@vueuse/core'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { Check, Columns2, Gauge, LoaderCircle, Search, X } from '@lucide/vue'
import { ART } from '@/assets/art'
import type { BlobRef, QaResult, VersionRow, VersionStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import EmptyState from '@/components/EmptyState.vue'
import Lightbox from '@/components/Lightbox.vue'
import CompareView, { type CompareItem } from '@/components/CompareView.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import QaBadge from '@/components/QaBadge.vue'
import { QA_LOW, QA_VERSION, groupQa, qaScore } from '@/composables/qa'
import { api } from '@/stores/connection'
import { featuredAcross, isAtlas, isImage, isJson, type AssetView } from '@/components/viewers/detect'
import { useAssetFile } from '@/composables/assetFile'
import { relativeTime } from '@/composables/format'
import { settlePool } from '@/composables/pool'
import { exportName, useVersions } from '@/stores/versions'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'

const versions = useVersions()
const runs = useRuns()
const recipes = useRecipes()
const toasts = useToasts()
const file = useAssetFile()

type AssetType = '2d' | '3d' | 'sprite' | 'spine' | 'texture' | 'audio'
type StatusFilter = 'all' | VersionStatus
type TypeFilter = 'all' | AssetType
type SortOrder = 'new' | 'score-desc' | 'score-asc'

interface AssetGroup {
  key: string
  name: string
  runId: string
  project: string
  recipe: string
  versions: VersionRow[]
  /** Thumbnail shown on the card. */
  cover: VersionRow
  /** What the lightbox opens: the GLB / Spine skeleton / sprite sheet when there is one. */
  lead: VersionRow
  type: AssetType
  status: VersionStatus
  technical: boolean
  created_at: number
  haystack: string
  qa: QaResult | null
  score: number | null
}

const STATUS_FILTERS: Array<{ id: StatusFilter; label: string }> = [
  { id: 'all', label: 'Tất cả' },
  { id: 'draft', label: 'Nháp' },
  { id: 'approved', label: 'Đã duyệt' },
  { id: 'rejected', label: 'Từ chối' }
]
const TYPE_FILTERS: Array<{ id: TypeFilter; label: string }> = [
  { id: 'all', label: 'Mọi loại' },
  { id: '2d', label: '2D' },
  { id: '3d', label: '3D' },
  { id: 'sprite', label: 'Sprite' },
  { id: 'spine', label: 'Spine' },
  { id: 'texture', label: 'Texture' },
  { id: 'audio', label: 'Âm thanh' }
]
const SORTS: Array<{ id: SortOrder; label: string }> = [
  { id: 'new', label: 'Mới nhất' },
  { id: 'score-desc', label: 'Điểm cao → thấp' },
  { id: 'score-asc', label: 'Điểm thấp → cao' }
]
const STATUS_TEXT: Record<VersionStatus, string> = { draft: 'Nháp', approved: 'Đã duyệt', rejected: 'Từ chối' }
const TYPE_TEXT: Record<AssetType, string> = { '2d': '2D', '3d': '3D', sprite: 'Sprite', spine: 'Spine', texture: 'Texture', audio: 'Âm thanh' }

const isTechnical = (b: BlobRef): boolean => isJson(b) || isAtlas(b)

function classify(f: AssetView | null, recipeId: string): AssetType {
  if (/^audio-/.test(recipeId)) return 'audio'
  if (f?.kind === 'tile' || /texture|tileset/.test(recipeId)) return 'texture'
  if (f?.kind === 'spine' || /spine/.test(recipeId)) return 'spine'
  if (f?.kind === 'sprite' || /sprite/.test(recipeId)) return 'sprite'
  if (f?.kind === 'glb' || /3d/.test(recipeId)) return '3d'
  return '2d'
}

function groupStatus(list: VersionRow[]): VersionStatus {
  if (list.some((v) => v.status === 'draft')) return 'draft'
  return list.some((v) => v.status === 'approved') ? 'approved' : 'rejected'
}

const runInfo = computed<Map<string, { recipeId: string; title: string }>>((prev) => {
  const next = new Map<string, { recipeId: string; title: string }>()
  let same = !!prev && prev.size === runs.runs.size
  for (const r of runs.runs.values()) {
    const old = prev?.get(r.id)
    if (same && (!old || old.recipeId !== r.recipe_id || old.title !== r.title)) same = false
    next.set(r.id, { recipeId: r.recipe_id, title: r.title })
  }
  return same && prev ? prev : next
})

const groups = computed<AssetGroup[]>(() => {
  const byKey = new Map<string, VersionRow[]>()
  for (const v of versions.list) {
    const key = `${v.run_id}\n${v.asset_name}`
    const arr = byKey.get(key)
    if (arr) arr.push(v)
    else byKey.set(key, [v])
  }
  const out: AssetGroup[] = []
  for (const [key, list] of byKey) {
    const first = list[0]!
    const run = runInfo.value.get(first.run_id)
    const recipeId = run?.recipeId ?? ''
    const recipe = recipes.byId(recipeId)?.name ?? ''
    const bySteps = new Map<string, BlobRef[]>()
    for (const v of list) bySteps.set(v.step_id, [...(bySteps.get(v.step_id) ?? []), v.blob])
    const featured = featuredAcross([...bySteps.values()])
    const byBlob = (h: string | undefined): VersionRow | undefined => list.find((v) => v.blob.hash === h)
    const images = list.filter((v) => isImage(v.blob))
    const cover = images.find((v) => /preview|render|front/i.test(v.blob.name ?? '')) ?? (featured?.kind === 'glb' ? undefined : byBlob(featured?.kind === 'spine' ? featured.spine?.texture.hash : featured?.blob.hash)) ?? images[0] ?? list.find((v) => !isTechnical(v.blob)) ?? first
    const lead = byBlob(featured?.blob.hash) ?? cover
    const qa = groupQa(lead, images)
    out.push({
      key,
      name: first.asset_name || run?.title || 'Không tên',
      runId: first.run_id,
      project: first.project_id,
      recipe,
      versions: list,
      cover,
      lead,
      type: classify(featured, recipeId),
      status: groupStatus(list),
      technical: list.every((v) => isTechnical(v.blob)),
      created_at: Math.max(...list.map((v) => v.created_at)),
      haystack: [first.asset_name, run?.title, recipe, ...list.map((v) => v.blob.name)].filter(Boolean).join(' ').toLowerCase(),
      qa,
      score: qaScore(qa)
    })
  }
  return out.sort((a, b) => b.created_at - a.created_at)
})

const statusFilter = ref<StatusFilter>('all')
const typeFilter = ref<TypeFilter>('all')
const query = ref('')
const sortOrder = ref<SortOrder>('new')
const lowOnly = ref(false)
const showTechnical = ref(false)
const route = useRoute()
const projectFilter = ref(typeof route.query.project === 'string' ? route.query.project : 'all')

const isLow = (g: AssetGroup): boolean => g.score !== null && g.score < QA_LOW
const unfiltered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return groups.value.filter(
    (g) =>
      (showTechnical.value || !g.technical) &&
      (typeFilter.value === 'all' || g.type === typeFilter.value) &&
      (projectFilter.value === 'all' || g.project === projectFilter.value) &&
      (!q || g.haystack.includes(q))
  )
})
const base = computed(() => (lowOnly.value ? unfiltered.value.filter(isLow) : unfiltered.value))
const technicalCount = computed(() => groups.value.filter((g) => g.technical).length)
const counts = computed(() => {
  const c: Record<StatusFilter, number> = { all: 0, draft: 0, approved: 0, rejected: 0 }
  for (const g of base.value) {
    c.all += 1
    c[g.status] += 1
  }
  return c
})
// Unscored assets sink to the end in both score orders; ties keep newest first.
function byScore(dir: 1 | -1): (a: AssetGroup, b: AssetGroup) => number {
  return (a, b) => {
    if (a.score === null || b.score === null) return a.score === b.score ? 0 : a.score === null ? 1 : -1
    return dir * (a.score - b.score) || b.created_at - a.created_at
  }
}
const items = computed(() => {
  const list = statusFilter.value === 'all' ? base.value : base.value.filter((g) => g.status === statusFilter.value)
  if (sortOrder.value === 'new') return list
  return [...list].sort(byScore(sortOrder.value === 'score-desc' ? -1 : 1))
})
const lowCount = computed(() => unfiltered.value.filter(isLow).length)
// Core scores new versions in the background right after a run; give that a minute before calling them "old".
const now = ref(Date.now())
useIntervalFn(() => (now.value = Date.now()), 15_000)
const unscored = computed(() => {
  const fresh = now.value / 1000 - 60
  return versions.list.filter(
    (v) => v.blob.mime.startsWith('image/') && v.qa?.version !== QA_VERSION && v.created_at < fresh && (projectFilter.value === 'all' || v.project_id === projectFilter.value)
  ).length
})
const scoring = ref(false)
async function scoreOld(): Promise<void> {
  if (scoring.value) return
  scoring.value = true
  try {
    const r = await api().recomputeQa({ missing_only: true, project_id: projectFilter.value === 'all' ? undefined : projectFilter.value, limit: 5000 })
    toasts.push(r.updated ? `Đã chấm điểm ${r.updated} ảnh` : 'Không còn ảnh nào cần chấm', 'success', 2400)
  } catch (e) {
    toasts.error(e, 'Không chấm điểm được')
  } finally {
    scoring.value = false
  }
}
const versionFor = (hash: string): VersionRow | undefined => current.value?.versions.find((v) => v.blob.hash === hash) ?? versions.byHash.get(hash)
const filtered = computed(() => !!query.value.trim() || typeFilter.value !== 'all' || statusFilter.value !== 'all' || projectFilter.value !== 'all' || lowOnly.value)

function clearFilters(): void {
  query.value = ''
  typeFilter.value = 'all'
  statusFilter.value = 'all'
  projectFilter.value = 'all'
  lowOnly.value = false
}

const scroller = ref<HTMLElement | null>(null)
const { width } = useElementSize(scroller)
const GAP = 16
const TILE_MIN = 176
const CAPTION_H = 58
const inner = computed(() => Math.max(0, width.value))
const cols = computed(() => Math.max(1, Math.floor((inner.value + GAP) / (TILE_MIN + GAP))))
const tileW = computed(() => (inner.value - GAP * (cols.value - 1)) / cols.value)
const rowH = computed(() => Math.round(tileW.value + CAPTION_H + GAP))
const rowCount = computed(() => Math.ceil(items.value.length / cols.value))

const virtualizer = useVirtualizer(
  computed(() => ({
    count: rowCount.value,
    getScrollElement: () => scroller.value,
    estimateSize: () => rowH.value,
    overscan: 3
  }))
)
watch(rowH, () => virtualizer.value.measure())
const vRows = computed(() => virtualizer.value.getVirtualItems())
const total = computed(() => virtualizer.value.getTotalSize())

function rowItems(r: number): AssetGroup[] {
  return items.value.slice(r * cols.value, r * cols.value + cols.value)
}

async function review(g: AssetGroup, action: 'approve' | 'reject'): Promise<void> {
  try {
    await versions.setStatusMany(
      g.versions.map((v) => v.id),
      action
    )
  } catch (e) {
    toasts.error(e, 'Không cập nhật được')
  }
}

const openKey = ref<string | null>(null)
const openOrigin = ref<DOMRect | null>(null)
function openTile(e: MouseEvent, key: string): void {
  openOrigin.value = (e.currentTarget as HTMLElement).getBoundingClientRect()
  openKey.value = key
}
const openIndex = computed(() => items.value.findIndex((g) => g.key === openKey.value))
const current = computed(() => (openIndex.value >= 0 ? items.value[openIndex.value] : undefined))
const lightboxMeta = computed(() => {
  const g = current.value
  if (!g) return ''
  return [g.recipe, relativeTime(g.created_at)].filter(Boolean).join(' · ')
})
// Reviewing can drop the open asset out of the status filter; keep the lightbox on its neighbour instead of closing.
// A/R from the keyboard (`advance`) moves on right away, while the request is still in flight.
async function reviewOpen(action: 'approve' | 'reject', advance = false): Promise<void> {
  const g = current.value
  if (!g) return
  const i = openIndex.value
  const after = items.value[i + 1]?.key ?? null
  const neighbour = after ?? items.value[i - 1]?.key ?? null
  const done = review(g, action)
  if (advance && after) {
    openOrigin.value = null
    openKey.value = after
  }
  await done
  if (openKey.value && !items.value.some((x) => x.key === openKey.value)) openKey.value = neighbour
}

const selected = shallowRef(new Set<string>())
let anchor: string | null = null
const selectedGroups = computed(() => items.value.filter((g) => selected.value.has(g.key)))
const selecting = computed(() => selected.value.size > 0)
watch(items, (list) => {
  if (!selected.value.size) return
  const keep = new Set(list.filter((g) => selected.value.has(g.key)).map((g) => g.key))
  if (keep.size !== selected.value.size) selected.value = keep
})

function clearSelection(): void {
  selected.value = new Set()
  anchor = null
}
function toggle(key: string): void {
  const next = new Set(selected.value)
  if (!next.delete(key)) next.add(key)
  selected.value = next
  anchor = key
}
function selectRange(key: string): void {
  const keys = items.value.map((g) => g.key)
  const a = anchor ? keys.indexOf(anchor) : -1
  const b = keys.indexOf(key)
  if (a < 0 || b < 0) return toggle(key)
  const next = new Set(selected.value)
  for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(k)
  selected.value = next
}
function onCheck(e: MouseEvent, key: string): void {
  if (e.shiftKey) selectRange(key)
  else toggle(key)
}
function onTile(e: MouseEvent, key: string): void {
  if (e.shiftKey) selectRange(key)
  else if (e.ctrlKey || e.metaKey || selecting.value) toggle(key)
  else openTile(e, key)
}

const bulk = ref<{ action: 'approve' | 'reject'; done: number; total: number } | null>(null)
const BULK_CONCURRENCY = 4
async function reviewSelected(action: 'approve' | 'reject'): Promise<void> {
  const list = selectedGroups.value
  if (!list.length || bulk.value) return
  bulk.value = { action, done: 0, total: list.length }
  const res = await settlePool(list, BULK_CONCURRENCY, async (g) => {
    try {
      await versions.setStatusMany(
        g.versions.map((v) => v.id),
        action
      )
    } finally {
      if (bulk.value) bulk.value.done += 1
    }
  })
  bulk.value = null
  const failed = list.filter((_, i) => res[i]?.status === 'rejected')
  const verb = action === 'approve' ? 'duyệt' : 'từ chối'
  if (failed.length) {
    toasts.push(`${failed.length}/${list.length} asset chưa ${verb} được, thử lại sau`, 'error', 6000)
    selected.value = new Set(failed.map((g) => g.key))
  } else {
    toasts.push(`Đã ${verb} ${list.length} asset`, 'success', 2400)
    clearSelection()
  }
}

const compareKeys = ref<string[] | null>(null)
function itemOf(g: AssetGroup): CompareItem {
  return {
    key: g.key,
    name: g.name,
    meta: [g.recipe, relativeTime(g.created_at)].filter(Boolean).join(' · '),
    status: g.status,
    lead: g.lead.blob,
    siblings: g.versions.map((v) => v.blob)
  }
}
const compareItems = computed<CompareItem[] | null>(() => {
  const keys = compareKeys.value
  if (!keys) return null
  const list = keys.map((k) => groups.value.find((g) => g.key === k)).filter((g): g is AssetGroup => !!g)
  return list.length === 2 ? list.map(itemOf) : null
})
// A compared group can vanish (filtered out, deleted); without the reset the grid's shortcuts stay blocked.
watch(compareItems, (items) => {
  if (!items && compareKeys.value) compareKeys.value = null
})
function openCompare(): void {
  if (selectedGroups.value.length === 2) compareKeys.value = selectedGroups.value.map((g) => g.key)
}
function reviewCompared(key: string, action: 'approve' | 'reject'): void {
  const g = groups.value.find((x) => x.key === key)
  if (g) void review(g, action)
}

useEventListener(window, 'keydown', (e: KeyboardEvent) => {
  if (openKey.value || compareKeys.value) return
  const t = e.target as HTMLElement | null
  if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
  if (e.key === 'Escape' && selecting.value) {
    clearSelection()
    e.preventDefault()
  } else if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A') && items.value.length) {
    selected.value = new Set(items.value.map((g) => g.key))
    e.preventDefault()
  }
})

function move(d: -1 | 1): void {
  const next = items.value[openIndex.value + d]
  if (next) {
    openOrigin.value = null
    openKey.value = next.key
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Thư viện">
      <template #subtitle><TweenNumber :value="base.length" /> asset</template>
    </PageHeader>
    <div class="bar">
      <label class="search">
        <Search :size="14" aria-hidden="true" />
        <input v-model="query" type="search" placeholder="Tìm theo tên, quy trình…" aria-label="Tìm asset" spellcheck="false" />
      </label>
      <div class="seg" role="radiogroup" aria-label="Loại asset">
        <button v-for="f in TYPE_FILTERS" :key="f.id" type="button" role="radio" :aria-checked="typeFilter === f.id" :class="{ on: typeFilter === f.id }" @click="typeFilter = f.id">
          {{ f.label }}
        </button>
      </div>
      <div class="seg" role="radiogroup" aria-label="Trạng thái duyệt">
        <button v-for="f in STATUS_FILTERS" :key="f.id" type="button" role="radio" :aria-checked="statusFilter === f.id" :class="{ on: statusFilter === f.id }" @click="statusFilter = f.id">
          {{ f.label }}
          <span class="num c">{{ counts[f.id] }}</span>
        </button>
      </div>
      <ProjectSelect v-model="projectFilter" all-label="Mọi dự án" label="Lọc theo dự án" />
      <label class="sort caption">
        <span class="sr-only">Sắp xếp</span>
        <select v-model="sortOrder" aria-label="Sắp xếp asset">
          <option v-for="o in SORTS" :key="o.id" :value="o.id">{{ o.label }}</option>
        </select>
      </label>
      <button
        type="button"
        class="chip-toggle"
        :class="{ on: lowOnly }"
        :aria-pressed="lowOnly"
        :title="`Chỉ hiện asset có điểm chất lượng dưới ${QA_LOW}`"
        @click="lowOnly = !lowOnly"
      >
        Điểm thấp
        <span class="num c">{{ lowCount }}</span>
      </button>
      <button v-if="unscored" type="button" class="btn btn-plain score-old" :disabled="scoring" title="Chấm điểm chất lượng cho các ảnh tạo trước khi có tính năng này" @click="scoreOld">
        <LoaderCircle v-if="scoring" class="spin" :size="14" />
        <Gauge v-else :size="14" />
        Chấm điểm {{ unscored }} ảnh cũ
      </button>
      <label v-if="technicalCount" class="toggle caption" title="Bản chỉ gồm JSON / Atlas (dữ liệu phụ của sprite, Spine)">
        <input v-model="showTechnical" type="checkbox" />
        Hiện tệp dữ liệu ({{ technicalCount }})
      </label>
    </div>
    <div ref="scroller" class="grid-scroll">
      <EmptyState
        v-if="versions.loaded && !versions.list.length"
        :art="ART.emptyGallery"
        title="Chưa có asset nào"
        text="Lượt chạy hoàn tất sẽ thêm bản nháp vào đây để bạn duyệt."
      >
        <RouterLink class="btn btn-primary" to="/new">Tạo asset mới</RouterLink>
      </EmptyState>
      <EmptyState v-else-if="versions.loaded && !items.length" :art="ART.emptyGallery" :art-size="120" title="Không có asset khớp bộ lọc">
        <button v-if="filtered" type="button" class="btn" @click="clearFilters">Xoá bộ lọc</button>
      </EmptyState>
      <div v-else class="inner" :style="{ height: `${total}px` }">
        <div
          v-for="r in vRows"
          :key="r.index"
          class="vrow"
          :style="{
            transform: `translateY(${r.start}px)`,
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`
          }"
        >
          <article v-for="g in rowItems(r.index)" :key="g.key" class="tile" :class="[`vs-${g.status}`, { sel: selected.has(g.key), selecting }]">
            <div class="stage">
              <button
                class="img-btn"
                :aria-label="selecting ? `Chọn ${g.name}` : `Xem ${g.name}`"
                :draggable="file.available"
                @click="onTile($event, g.key)"
                @pointerenter="file.prepare(g.lead.blob.hash, exportName(g.lead))"
                @dragstart="file.startDrag($event, g.lead.blob.hash, exportName(g.lead))"
              >
                <BlobThumb :blob="g.cover.blob" :size="256" />
                <span class="type-tag">{{ TYPE_TEXT[g.type] }}</span>
                <span class="img-ring" aria-hidden="true" />
                <span v-if="g.versions.length > 1" class="more num" :title="g.versions.map((v) => v.blob.name).filter(Boolean).join('\n')">+{{ g.versions.length - 1 }} file</span>
              </button>
              <button
                type="button"
                class="pick"
                role="checkbox"
                :aria-checked="selected.has(g.key)"
                :aria-label="`Chọn ${g.name}`"
                title="Chọn (Shift: chọn dải, Ctrl: thêm/bớt)"
                @click="onCheck($event, g.key)"
              >
                <Check :size="13" :stroke-width="3" />
              </button>
              <div class="acts" role="group" :aria-label="`Duyệt ${g.name}`">
                <button type="button" class="act ok" :class="{ on: g.status === 'approved' }" :aria-pressed="g.status === 'approved'" @click="review(g, 'approve')">
                  <Check :size="14" :stroke-width="2.4" />
                  Duyệt
                </button>
                <button type="button" class="act no" :class="{ on: g.status === 'rejected' }" :aria-pressed="g.status === 'rejected'" @click="review(g, 'reject')">
                  <X :size="14" :stroke-width="2.4" />
                  Từ chối
                </button>
              </div>
            </div>
            <div class="meta">
              <div class="m1">
                <span class="name" :title="g.name">{{ g.name }}</span>
                <QaBadge :qa="g.qa" />
                <Transition name="fade" mode="out-in">
                  <span :key="g.status" class="pill">{{ STATUS_TEXT[g.status] }}</span>
                </Transition>
              </div>
              <p class="caption sub">
                <template v-if="g.recipe">{{ g.recipe }} · </template>
                <RelTime :ts="g.created_at" />
              </p>
            </div>
          </article>
        </div>
      </div>
    </div>
    <Transition name="selbar">
      <div v-if="selecting" class="selbar" role="toolbar" aria-label="Thao tác với asset đã chọn">
        <span class="sel-count">
          <TweenNumber :value="selected.size" /> đã chọn
        </span>
        <span class="sep" />
        <template v-if="bulk">
          <span class="caption bulk-state" role="status">
            <LoaderCircle class="spin" :size="14" />
            Đang {{ bulk.action === 'approve' ? 'duyệt' : 'từ chối' }} <TweenNumber :value="bulk.done" />/{{ bulk.total }}
          </span>
        </template>
        <template v-else>
          <button type="button" class="btn sb ok" @click="reviewSelected('approve')"><Check /> Duyệt <TweenNumber :value="selectedGroups.length" /></button>
          <button type="button" class="btn sb no" @click="reviewSelected('reject')"><X /> Từ chối <TweenNumber :value="selectedGroups.length" /></button>
          <button
            type="button"
            class="btn sb"
            :disabled="selectedGroups.length !== 2"
            :title="selectedGroups.length === 2 ? 'Xem 2 bản cạnh nhau' : 'Chọn đúng 2 asset để so sánh'"
            @click="openCompare"
          >
            <Columns2 /> So sánh
          </button>
        </template>
        <button type="button" class="btn btn-plain sb" :disabled="!!bulk" title="Bỏ chọn (Esc)" @click="clearSelection">Bỏ chọn</button>
      </div>
    </Transition>
    <CompareView :items="compareItems" @close="compareKeys = null" @review="reviewCompared" />
    <Lightbox
      :blob="current?.lead.blob ?? null"
      :siblings="current?.versions.map((v) => v.blob) ?? []"
      :title="current?.name"
      :meta="lightboxMeta"
      :status="current?.status"
      :run-id="current?.runId"
      :version-id="current?.lead.id"
      :filename="current ? exportName(current.lead) : undefined"
      :index="openIndex"
      :count="items.length"
      @prev="move(-1)"
      @next="move(1)"
      @review="reviewOpen"
      :origin="openOrigin"
      :version-for="versionFor"
      @close="openKey = null"
    />
  </div>
</template>

<style scoped>
.bar {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
  padding: 12px 24px;
  border-bottom: 0.5px solid var(--separator);
}
.search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 240px;
  min-width: 160px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  color: var(--text-3);
  background: var(--bg-control);
  box-shadow: inset 0 0 0 0.5px var(--separator-strong);
  transition: box-shadow var(--t-fast);
}
.search:focus-within {
  box-shadow: 0 0 0 3px var(--focus-ring), var(--hairline);
}
.search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--text);
  user-select: text;
}
.search input::placeholder {
  color: var(--text-3);
}
.sort select {
  height: 26px;
  padding: 0 6px;
  border: 0;
  border-radius: 6px;
  color: var(--text);
  background: var(--bg-control);
  box-shadow: var(--control-shadow);
}
.chip-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  font-weight: 400;
  color: var(--text);
  background: var(--bg-control);
  box-shadow: var(--control-shadow);
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    color var(--t-fast);
}
.chip-toggle.on {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.score-old {
  height: 26px;
  gap: 6px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
.c {
  font-size: 11px;
  color: var(--text-3);
}
.toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  cursor: pointer;
}
.toggle input {
  accent-color: var(--accent);
  margin: 0;
}
.grid-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 24px;
}
.inner {
  position: relative;
}
.vrow {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: grid;
  gap: 16px;
}
.tile {
  position: relative;
  border-radius: var(--r-lg);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  overflow: hidden;
  content-visibility: auto;
  contain-intrinsic-size: 280px;
  transition:
    transform var(--t-base) var(--ease-spring),
    box-shadow var(--t-fast),
    opacity var(--t-base);
}
.tile:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2), var(--hairline);
}
.tile.sel {
  box-shadow: var(--shadow-2), 0 0 0 2px var(--accent);
}
.img-btn :deep(.blob),
.img-btn :deep(.glyph) {
  transition: transform var(--t-base) var(--ease-spring);
}
.tile.sel .img-btn :deep(.blob),
.tile.sel .img-btn :deep(.glyph) {
  transform: scale(0.92);
}
.selecting .img-btn {
  cursor: pointer;
}
.img-ring {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: var(--accent-soft);
  opacity: 0;
  transition: opacity var(--t-base);
}
.tile.sel .img-ring {
  opacity: 0.5;
}
.pick {
  position: absolute;
  top: 7px;
  left: 7px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  color: transparent;
  background: color-mix(in srgb, var(--bg-elevated) 70%, transparent);
  box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--text) 45%, transparent), var(--shadow-1);
  backdrop-filter: blur(8px);
  cursor: pointer;
  opacity: 0;
  transform: scale(0.8);
  transition:
    opacity var(--t-fast),
    transform var(--t-fast) var(--ease-spring),
    background-color var(--t-fast),
    color var(--t-fast);
}
.tile:hover .pick,
.tile:focus-within .pick,
.selecting .pick {
  opacity: 1;
  transform: none;
}
.pick[aria-checked='true'] {
  color: var(--text-on-accent);
  background: var(--accent);
  box-shadow: var(--shadow-1);
}
.pick:active {
  transform: scale(0.9);
}
.tile:hover .type-tag,
.tile:focus-within .type-tag,
.selecting .type-tag {
  transform: translateX(26px);
}
.selbar {
  position: absolute;
  z-index: 20;
  left: 50%;
  bottom: 20px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 6px 6px 14px;
  border-radius: 14px;
  transform: translateX(-50%);
  background: color-mix(in srgb, var(--bg-elevated) 86%, transparent);
  backdrop-filter: blur(18px) saturate(1.4);
  box-shadow: var(--shadow-3), var(--hairline);
  white-space: nowrap;
}
.sel-count {
  font-weight: 600;
  min-width: 78px;
}
.selbar .sep {
  width: 0.5px;
  height: 20px;
  margin: 0 4px;
  background: var(--separator-strong);
}
.sb {
  height: 26px;
  padding: 0 12px;
  font-size: var(--fs-callout);
}
.sb.ok {
  color: var(--st-ok);
}
.sb.no {
  color: var(--st-fail);
}
.bulk-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  color: var(--text-2);
}
.spin {
  animation: spin 1s linear infinite;
}
.selbar-enter-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-slow) var(--ease-spring);
}
.selbar-leave-active {
  transition:
    opacity var(--t-fast) var(--ease-in),
    transform var(--t-fast) var(--ease-in);
}
.selbar-enter-from,
.selbar-leave-to {
  opacity: 0;
  transform: translate(-50%, 16px);
}
.vs-rejected .img-btn {
  opacity: 0.5;
}
.img-btn {
  position: relative;
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  cursor: zoom-in;
  transition: opacity var(--t-base);
}
.img-btn:focus-visible {
  outline-offset: -2px;
}
.type-tag,
.more {
  position: absolute;
  top: 8px;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text);
  background: color-mix(in srgb, var(--bg-elevated) 84%, transparent);
  backdrop-filter: blur(10px);
  box-shadow: var(--hairline);
}
.type-tag {
  left: 8px;
  transition: transform var(--t-fast) var(--ease-spring);
}
.more {
  right: 8px;
  color: var(--text-2);
}
.meta {
  display: grid;
  align-content: start;
  gap: 2px;
  padding: 9px 12px 0;
  height: 58px;
}
.m1 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pill {
  flex: none;
  font-size: var(--fs-caption);
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  color: var(--text-2);
  background: var(--bg-active);
}
.vs-approved .pill {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.vs-rejected .pill {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.vs-draft .pill {
  color: var(--accent);
  background: var(--accent-soft);
}
.stage {
  position: relative;
}
.acts {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 8px;
  display: flex;
  gap: 6px;
  opacity: 0;
  transform: translateY(4px);
  transition:
    opacity var(--t-fast),
    transform var(--t-fast) var(--ease-out);
}
.tile:hover .acts,
.tile:focus-within .acts {
  opacity: 1;
  transform: none;
}
.act {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 28px;
  border: 0;
  border-radius: var(--r-sm);
  font-size: var(--fs-callout);
  font-weight: 550;
  cursor: pointer;
  color: var(--text);
  background: color-mix(in srgb, var(--bg-elevated) 86%, transparent);
  backdrop-filter: blur(12px);
  box-shadow: var(--shadow-1), var(--hairline);
  transition:
    transform var(--t-fast) var(--ease-spring),
    background-color var(--t-fast),
    color var(--t-fast);
}
.act:active {
  transform: scale(0.96);
}
.act.ok:hover,
.act.ok.on {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.act.no:hover,
.act.no.on {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.view {
  position: relative;
}
@media (prefers-reduced-motion: reduce) {
  .tile,
  .acts,
  .pick,
  .type-tag,
  .img-btn :deep(.blob),
  .img-btn :deep(.glyph) {
    transition: none;
  }
  .tile:hover {
    transform: none;
  }
}
</style>
