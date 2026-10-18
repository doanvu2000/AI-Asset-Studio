<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { Check, X } from '@lucide/vue'
import { ART } from '@/assets/art'
import type { BlobRef, VersionRow, VersionStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import EmptyState from '@/components/EmptyState.vue'
import Lightbox from '@/components/Lightbox.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useVersions } from '@/stores/versions'
import { useToasts } from '@/stores/toasts'
import RelTime from '@/components/RelTime.vue'

const versions = useVersions()
const toasts = useToasts()

type Filter = 'all' | VersionStatus
const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: 'all', label: 'Tất cả' },
  { id: 'draft', label: 'Nháp' },
  { id: 'approved', label: 'Đã duyệt' },
  { id: 'rejected', label: 'Từ chối' }
]
const STATUS_TEXT: Record<VersionStatus, string> = { draft: 'Nháp', approved: 'Đã duyệt', rejected: 'Từ chối' }

const filter = ref<Filter>('all')
const counts = computed(() => {
  const c: Record<Filter, number> = { all: 0, draft: 0, approved: 0, rejected: 0 }
  for (const v of versions.list) {
    c.all += 1
    c[v.status] += 1
  }
  return c
})
const items = computed(() => (filter.value === 'all' ? versions.list : versions.list.filter((v) => v.status === filter.value)))

const scroller = ref<HTMLElement | null>(null)
const { width } = useElementSize(scroller)
const GAP = 16
const TILE_MIN = 168
const CAPTION_H = 52
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

function rowItems(r: number): VersionRow[] {
  return items.value.slice(r * cols.value, r * cols.value + cols.value)
}

async function set(v: VersionRow, action: 'approve' | 'reject'): Promise<void> {
  try {
    await versions.setStatus(v.id, action)
  } catch (e) {
    toasts.error(e, 'Không cập nhật được')
  }
}

const lightbox = ref<BlobRef | null>(null)
const lightboxRun = ref('')
// Sibling versions of the same run let the viewer pair a sheet with its JSON or a Spine skeleton with its atlas.
const siblings = computed(() => (lightboxRun.value ? versions.list.filter((v) => v.run_id === lightboxRun.value).map((v) => v.blob) : []))
function open(v: VersionRow): void {
  lightboxRun.value = v.run_id
  lightbox.value = v.blob
}
</script>

<template>
  <div class="view">
    <PageHeader title="Thư viện">
      <template #subtitle><TweenNumber :value="counts.all" /> phiên bản</template>
    </PageHeader>
    <div class="bar">
      <div class="segmented" role="tablist" aria-label="Lọc phiên bản">
        <button
          v-for="f in FILTERS"
          :key="f.id"
          role="tab"
          :aria-selected="filter === f.id"
          :class="{ on: filter === f.id }"
          @click="filter = f.id"
        >
          {{ f.label }}
          <span class="num c">{{ counts[f.id] }}</span>
        </button>
      </div>
    </div>
    <div ref="scroller" class="grid-scroll">
      <EmptyState
        v-if="versions.loaded && !items.length"
        :art="ART.emptyGallery"
        title="Chưa có phiên bản nào"
        text="Run hoàn tất sẽ thêm bản nháp vào đây để bạn duyệt."
      />
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
          <article v-for="v in rowItems(r.index)" :key="v.id" class="tile" :class="`vs-${v.status}`">
            <button class="img-btn" :aria-label="`Xem ${v.asset_name}`" @click="open(v)">
              <BlobThumb :blob="v.blob" :size="256" />
            </button>
            <div class="meta">
              <div class="m1">
                <span class="name">{{ v.asset_name }}</span>
                <Transition name="fade" mode="out-in">
                  <span :key="v.status" class="pill">{{ STATUS_TEXT[v.status] }}</span>
                </Transition>
              </div>
              <RelTime class="caption" :ts="v.created_at" />
            </div>
            <div class="acts">
              <button
                class="act ok"
                :class="{ on: v.status === 'approved' }"
                :aria-pressed="v.status === 'approved'"
                aria-label="Duyệt"
                title="Duyệt"
                @click="set(v, 'approve')"
              >
                <Check :size="14" :stroke-width="2.6" />
              </button>
              <button
                class="act no"
                :class="{ on: v.status === 'rejected' }"
                :aria-pressed="v.status === 'rejected'"
                aria-label="Từ chối"
                title="Từ chối"
                @click="set(v, 'reject')"
              >
                <X :size="14" :stroke-width="2.6" />
              </button>
            </div>
          </article>
        </div>
      </div>
    </div>
    <Lightbox :blob="lightbox" :siblings="siblings" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.bar {
  flex: none;
  padding: 12px 24px;
  border-bottom: 0.5px solid var(--separator);
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
  padding: 0 12px;
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
  contain-intrinsic-size: 220px;
  transition:
    transform var(--t-base) var(--ease-spring),
    box-shadow var(--t-fast),
    opacity var(--t-base);
}
.tile:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2), var(--hairline);
}
.vs-rejected {
  opacity: 0.55;
}
.img-btn {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  cursor: zoom-in;
}
.meta {
  display: grid;
  gap: 1px;
  padding: 8px 10px 9px;
  height: 52px;
}
.m1 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.name {
  flex: 1;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pill {
  font-size: 10.5px;
  font-weight: 650;
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
.acts {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 6px;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    opacity var(--t-fast),
    transform var(--t-fast) var(--ease-out);
}
.tile:hover .acts,
.tile:focus-within .acts,
.vs-draft .acts {
  opacity: 1;
  transform: none;
}
.act {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  color: var(--text);
  background: color-mix(in srgb, var(--bg-elevated) 82%, transparent);
  backdrop-filter: blur(10px);
  box-shadow: var(--shadow-1), var(--hairline);
  transition:
    transform var(--t-fast) var(--ease-spring),
    background-color var(--t-fast);
}
.act:active {
  transform: scale(0.9);
}
.act.ok.on,
.act.ok:hover {
  color: #fff;
  background: var(--st-ok);
}
.act.no.on,
.act.no:hover {
  color: #fff;
  background: var(--st-fail);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
