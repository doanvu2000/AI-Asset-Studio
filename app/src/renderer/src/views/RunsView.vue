<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { onKeyStroke } from '@vueuse/core'
import { Columns2, Plus, X } from '@lucide/vue'
import { ART } from '@/assets/art'
import { ILLUS } from '@/assets/illustrations'
import type { RunStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import SectionTabs from '@/components/SectionTabs.vue'
import { HISTORY_TABS } from '@/composables/navTabs'
import RunRowItem from '@/components/RunRowItem.vue'
import RunDiff from '@/components/RunDiff.vue'
import EmptyState from '@/components/EmptyState.vue'
import ProjectSelect from '@/components/ProjectSelect.vue'
import { useRuns } from '@/stores/runs'

const runs = useRuns()
const route = useRoute()
const projectFilter = ref(typeof route.query.project === 'string' ? route.query.project : 'all')
// The list holds the recent window only; a project's older runs are fetched once it is picked.
watch(projectFilter, (p) => p !== 'all' && p !== 'default' && void runs.loadProject(p).catch(() => undefined), { immediate: true })
const inProject = computed(() => (projectFilter.value === 'all' ? runs.runList : runs.runList.filter((r) => r.project_id === projectFilter.value)))

type Filter = 'all' | 'active' | 'awaiting_human' | 'succeeded' | 'failed'
const FILTERS: Array<{ id: Filter; label: string; match: (s: RunStatus) => boolean }> = [
  { id: 'all', label: 'Tất cả', match: () => true },
  { id: 'active', label: 'Đang chạy', match: (s) => s === 'running' },
  { id: 'awaiting_human', label: 'Chờ duyệt', match: (s) => s === 'awaiting_human' },
  { id: 'succeeded', label: 'Hoàn tất', match: (s) => s === 'succeeded' },
  { id: 'failed', label: 'Lỗi / huỷ', match: (s) => s === 'failed' || s === 'cancelled' || s === 'interrupted' }
]

function readFilter(): Filter {
  try {
    return (sessionStorage.getItem('studio.runs.filter') as Filter | null) ?? 'all'
  } catch {
    return 'all'
  }
}
const filter = ref<Filter>(readFilter())
watch(filter, (f) => {
  try {
    sessionStorage.setItem('studio.runs.filter', f)
  } catch {
    /* storage unavailable */
  }
})

const counts = computed(() => {
  const out: Record<Filter, number> = { all: 0, active: 0, awaiting_human: 0, succeeded: 0, failed: 0 }
  for (const r of inProject.value) for (const f of FILTERS) if (f.match(r.status)) out[f.id] += 1
  return out
})
const rows = computed(() => {
  const f = FILTERS.find((x) => x.id === filter.value)!
  return inProject.value.filter((r) => f.match(r.status))
})

// Two picks max: a third pick drops the oldest so the bar never needs an error state.
const picked = ref<string[]>([])
function togglePick(id: string): void {
  picked.value = picked.value.includes(id) ? picked.value.filter((x) => x !== id) : [...picked.value, id].slice(-2)
}
const diffPair = ref<[string, string] | null>(null)
function openDiff(): void {
  if (picked.value.length === 2) diffPair.value = [picked.value[0]!, picked.value[1]!]
}
onKeyStroke('Escape', (e) => {
  const t = e.target as HTMLElement | null
  if (diffPair.value || !picked.value.length || t?.closest('input:not([type=checkbox]), textarea, select')) return
  picked.value = []
})

const scroller = ref<HTMLElement | null>(null)
const ROW_H = 60
const virtualizer = useVirtualizer(
  computed(() => ({
    count: rows.value.length,
    getScrollElement: () => scroller.value,
    estimateSize: () => ROW_H,
    overscan: 8,
    getItemKey: (i: number) => rows.value[i]?.id ?? i
  }))
)
const items = computed(() => virtualizer.value.getVirtualItems())
const total = computed(() => virtualizer.value.getTotalSize())
const NEAR_END = 12
watch(
  () => (items.value.length ? items.value[items.value.length - 1]!.index : -1) >= rows.value.length - NEAR_END,
  (near) => near && void runs.loadMore().catch(() => undefined),
  { flush: 'post' }
)
// A filter can leave the loaded window with few rows while older runs still exist.
watch([filter, projectFilter], () => rows.value.length < NEAR_END && void runs.loadMore().catch(() => undefined))
</script>

<template>
  <div class="view">
    <PageHeader title="Lượt chạy" :subtitle="`${runs.runList.length} lượt`">
      <RouterLink to="/new" class="btn btn-primary" aria-label="Tạo mới" title="Tạo mới"><Plus /> <span class="lbl">Tạo mới</span></RouterLink>
    </PageHeader>
    <SectionTabs :tabs="HISTORY_TABS" current="/runs" label="Lịch sử" />
    <div class="bar">
      <div class="seg" role="tablist" aria-label="Lọc theo trạng thái">
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
      <ProjectSelect v-model="projectFilter" all-label="Mọi dự án" label="Lọc theo dự án" />
    </div>
    <div v-if="picked.length" class="pickbar" role="region" aria-label="So sánh lượt chạy">
      <span class="num">Đã chọn {{ picked.length }}/2</span>
      <span v-if="picked.length < 2" class="caption">Chọn thêm 1 lượt để so sánh</span>
      <span class="grow" />
      <button type="button" class="btn btn-plain" @click="picked = []"><X /> Bỏ chọn</button>
      <button type="button" class="btn btn-primary" :disabled="picked.length < 2" @click="openDiff"><Columns2 /> So sánh 2 lượt</button>
    </div>
    <div ref="scroller" class="list-scroll">
      <EmptyState
        v-if="runs.listLoaded && !rows.length && (filter !== 'all' || projectFilter !== 'all')"
        :art="ART.emptyRuns"
        :art-size="128"
        title="Không có lượt chạy nào ở mục này"
      >
        <button class="btn" @click="(filter = 'all'), (projectFilter = 'all')">Xem tất cả</button>
        <RouterLink to="/new" class="btn btn-primary"><Plus /> Tạo asset mới</RouterLink>
      </EmptyState>
      <EmptyState
        v-else-if="runs.listLoaded && !rows.length"
        :illustration="ILLUS.emptyHistory"
        title="Chưa có lượt chạy nào"
        text="Chọn một quy trình, nhập mô tả và bấm chạy. Lượt chạy sẽ hiện ở đây."
        action-label="Tạo asset mới"
        action-to="/new"
        :action-icon="Plus"
      />
      <div v-else class="inner" :style="{ height: `${total}px` }">
        <div
          v-for="it in items"
          :key="String(it.key)"
          class="vrow"
          :class="{ first: it.index === 0 }"
          :style="{ transform: `translateY(${it.start}px)`, height: `${ROW_H}px` }"
        >
          <RunRowItem
            :run="rows[it.index]!"
            menu
            selectable
            :selected="picked.includes(rows[it.index]!.id)"
            @select="togglePick(rows[it.index]!.id)"
          />
        </div>
      </div>
      <div v-if="runs.listHasMore && rows.length" class="more">
        <button class="btn" :disabled="runs.listMoreLoading" @click="runs.loadMore().catch(() => undefined)">
          {{ runs.listMoreLoading ? 'Đang tải…' : 'Tải thêm' }}
        </button>
      </div>
    </div>
    <RunDiff :a="diffPair?.[0] ?? null" :b="diffPair?.[1] ?? null" @close="diffPair = null" />
  </div>
</template>

<style scoped>
.bar {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-6) 0;
}
.c {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.pickbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  max-width: 1180px;
  width: calc(100% - 2 * var(--sp-6));
  box-sizing: border-box;
  margin: var(--sp-3) auto 0;
  padding: 6px 8px 6px 14px;
  border-radius: var(--r-lg);
  background: var(--accent-soft);
  box-shadow: var(--hairline);
  animation: fade-up var(--t-base) var(--ease-out) backwards;
}
.grow {
  flex: 1;
}
.list-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--sp-3) var(--sp-6) var(--sp-6);
  contain: strict;
}
.inner {
  position: relative;
  max-width: 1180px;
  margin: 0 auto;
  overflow: hidden;
  border-radius: var(--r-lg);
  background: var(--surface);
}
.inner :deep(.row) {
  border-radius: 0;
  padding: 0 var(--sp-4);
}
.more {
  display: flex;
  justify-content: center;
  padding: var(--sp-4) 0 0;
}
.vrow {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  border-top: 1px solid var(--hair-soft);
}
.vrow.first {
  border-top: 0;
}
</style>
