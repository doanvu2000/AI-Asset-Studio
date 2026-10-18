<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { ListChecks, Plus } from '@lucide/vue'
import type { RunStatus } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import RunRowItem from '@/components/RunRowItem.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useRuns } from '@/stores/runs'

const runs = useRuns()

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
  for (const r of runs.runList) for (const f of FILTERS) if (f.match(r.status)) out[f.id] += 1
  return out
})
const rows = computed(() => {
  const f = FILTERS.find((x) => x.id === filter.value)!
  return runs.runList.filter((r) => f.match(r.status))
})

const scroller = ref<HTMLElement | null>(null)
const ROW_H = 62
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
</script>

<template>
  <div class="view">
    <PageHeader title="Lượt chạy" :subtitle="`${runs.runList.length} run`">
      <RouterLink to="/new" class="btn btn-primary"><Plus /> Tạo mới</RouterLink>
    </PageHeader>
    <div class="bar">
      <div class="segmented" role="tablist" aria-label="Lọc theo trạng thái">
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
    <div ref="scroller" class="list-scroll">
      <EmptyState v-if="runs.listLoaded && !rows.length" :icon="ListChecks" title="Không có run nào ở mục này" />
      <div v-else class="inner" :style="{ height: `${total}px` }">
        <div
          v-for="it in items"
          :key="String(it.key)"
          class="vrow"
          :style="{ transform: `translateY(${it.start}px)`, height: `${ROW_H}px` }"
        >
          <RunRowItem :run="rows[it.index]!" />
        </div>
      </div>
    </div>
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
.list-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px 16px;
  contain: strict;
}
.inner {
  position: relative;
  max-width: 1180px;
  margin: 0 auto;
}
.vrow {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  border-bottom: 0.5px solid var(--separator);
}
</style>
