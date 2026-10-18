<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { Check, Search, X } from '@lucide/vue'
import { ART } from '@/assets/art'
import type { BlobRef, VersionRow, VersionStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import EmptyState from '@/components/EmptyState.vue'
import Lightbox from '@/components/Lightbox.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import { featuredAsset, isAtlas, isImage, isJson } from '@/components/viewers/detect'
import { useAssetFile } from '@/composables/assetFile'
import { relativeTime } from '@/composables/format'
import { exportName, useVersions } from '@/stores/versions'
import { useRuns } from '@/stores/runs'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'

const versions = useVersions()
const runs = useRuns()
const recipes = useRecipes()
const toasts = useToasts()
const file = useAssetFile()

type AssetType = '2d' | '3d' | 'sprite' | 'spine'
type StatusFilter = 'all' | VersionStatus
type TypeFilter = 'all' | AssetType

interface AssetGroup {
  key: string
  name: string
  runId: string
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
  { id: 'spine', label: 'Spine' }
]
const STATUS_TEXT: Record<VersionStatus, string> = { draft: 'Nháp', approved: 'Đã duyệt', rejected: 'Từ chối' }
const TYPE_TEXT: Record<AssetType, string> = { '2d': '2D', '3d': '3D', sprite: 'Sprite', spine: 'Spine' }

const isTechnical = (b: BlobRef): boolean => isJson(b) || isAtlas(b)

function classify(blobs: BlobRef[], recipeId: string): AssetType {
  const f = featuredAsset(blobs)
  if (f?.kind === 'spine' || /spine/.test(recipeId)) return 'spine'
  if (f?.kind === 'sprite' || /sprite/.test(recipeId)) return 'sprite'
  if (f?.kind === 'glb' || /3d/.test(recipeId)) return '3d'
  return '2d'
}

function groupStatus(list: VersionRow[]): VersionStatus {
  if (list.some((v) => v.status === 'draft')) return 'draft'
  return list.some((v) => v.status === 'approved') ? 'approved' : 'rejected'
}

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
    const run = runs.runs.get(first.run_id)
    const recipeId = run?.recipe_id ?? ''
    const recipe = recipes.byId(recipeId)?.name ?? ''
    const blobs = list.map((v) => v.blob)
    const featured = featuredAsset(blobs)
    const byBlob = (h: string | undefined): VersionRow | undefined => list.find((v) => v.blob.hash === h)
    const images = list.filter((v) => isImage(v.blob))
    const cover = images.find((v) => /preview|render|front/i.test(v.blob.name ?? '')) ?? (featured?.kind === 'glb' ? undefined : byBlob(featured?.kind === 'spine' ? featured.spine?.texture.hash : featured?.blob.hash)) ?? images[0] ?? list.find((v) => !isTechnical(v.blob)) ?? first
    out.push({
      key,
      name: first.asset_name || run?.title || 'Không tên',
      runId: first.run_id,
      recipe,
      versions: list,
      cover,
      lead: byBlob(featured?.blob.hash) ?? cover,
      type: classify(blobs, recipeId),
      status: groupStatus(list),
      technical: list.every((v) => isTechnical(v.blob)),
      created_at: Math.max(...list.map((v) => v.created_at)),
      haystack: [first.asset_name, run?.title, recipe, ...list.map((v) => v.blob.name)].filter(Boolean).join(' ').toLowerCase()
    })
  }
  return out.sort((a, b) => b.created_at - a.created_at)
})

const statusFilter = ref<StatusFilter>('all')
const typeFilter = ref<TypeFilter>('all')
const query = ref('')
const showTechnical = ref(false)

const base = computed(() => {
  const q = query.value.trim().toLowerCase()
  return groups.value.filter(
    (g) => (showTechnical.value || !g.technical) && (typeFilter.value === 'all' || g.type === typeFilter.value) && (!q || g.haystack.includes(q))
  )
})
const technicalCount = computed(() => groups.value.filter((g) => g.technical).length)
const counts = computed(() => {
  const c: Record<StatusFilter, number> = { all: 0, draft: 0, approved: 0, rejected: 0 }
  for (const g of base.value) {
    c.all += 1
    c[g.status] += 1
  }
  return c
})
const items = computed(() => (statusFilter.value === 'all' ? base.value : base.value.filter((g) => g.status === statusFilter.value)))
const filtered = computed(() => !!query.value.trim() || typeFilter.value !== 'all' || statusFilter.value !== 'all')

function clearFilters(): void {
  query.value = ''
  typeFilter.value = 'all'
  statusFilter.value = 'all'
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
async function reviewOpen(action: 'approve' | 'reject'): Promise<void> {
  const g = current.value
  if (!g) return
  const i = openIndex.value
  const neighbour = items.value[i + 1]?.key ?? items.value[i - 1]?.key ?? null
  await review(g, action)
  if (!items.value.some((x) => x.key === g.key)) openKey.value = neighbour
}

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
      <div class="segmented" role="radiogroup" aria-label="Loại asset">
        <button v-for="f in TYPE_FILTERS" :key="f.id" type="button" role="radio" :aria-checked="typeFilter === f.id" :class="{ on: typeFilter === f.id }" @click="typeFilter = f.id">
          {{ f.label }}
        </button>
      </div>
      <div class="segmented" role="radiogroup" aria-label="Trạng thái duyệt">
        <button v-for="f in STATUS_FILTERS" :key="f.id" type="button" role="radio" :aria-checked="statusFilter === f.id" :class="{ on: statusFilter === f.id }" @click="statusFilter = f.id">
          {{ f.label }}
          <span class="num c">{{ counts[f.id] }}</span>
        </button>
      </div>
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
          <article v-for="g in rowItems(r.index)" :key="g.key" class="tile" :class="`vs-${g.status}`">
            <div class="stage">
              <button
                class="img-btn"
                :aria-label="`Xem ${g.name}`"
                :draggable="file.available"
                @click="openTile($event, g.key)"
                @pointerenter="file.prepare(g.lead.blob.hash, exportName(g.lead))"
                @dragstart="file.startDrag($event, g.lead.blob.hash, exportName(g.lead))"
              >
                <BlobThumb :blob="g.cover.blob" :size="256" />
                <span class="type-tag">{{ TYPE_TEXT[g.type] }}</span>
                <span v-if="g.versions.length > 1" class="more num" :title="g.versions.map((v) => v.blob.name).filter(Boolean).join('\n')">+{{ g.versions.length - 1 }} file</span>
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
  height: 30px;
  padding: 0 10px;
  border-radius: 8px;
  color: var(--text-3);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
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
.segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.segmented button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 11px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-weight: 500;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.segmented button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
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
@media (prefers-reduced-motion: reduce) {
  .tile,
  .acts {
    transition: none;
  }
  .tile:hover {
    transform: none;
  }
}
</style>
