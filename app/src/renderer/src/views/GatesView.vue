<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ART } from '@/assets/art'
import { ExternalLink } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import EmptyState from '@/components/EmptyState.vue'
import GatePicker from '@/components/gate/GatePicker.vue'
import GateApprove from '@/components/gate/GateApprove.vue'
import { isImage } from '@/components/viewers/detect'
import type { BlobRef } from '@/api/types'
import { useGates, type GateItem } from '@/stores/gates'
import { useRuns } from '@/stores/runs'
import RelTime from '@/components/RelTime.vue'
import ResizeHandle from '@/components/ResizeHandle.vue'
import { usePaneWidth } from '@/composables/paneSize'
import { runMeta } from '@/stores/batches'

const gates = useGates()
const runs = useRuns()
const route = useRoute()
const router = useRouter()
const queueW = usePaneWidth('gates-queue', 280)

const batchId = computed(() => (route.query.batch as string | undefined) ?? null)
const queue = computed<GateItem[]>(() => {
  if (!batchId.value) return gates.list
  const inBatch = gates.list.filter((g) => { const r = runs.runs.get(g.runId); return !!r && runMeta(r).batch_id === batchId.value })
  return inBatch.length ? inBatch : gates.list
})
const currentId = ref<string | null>((route.query.step as string | undefined) ?? null)
const current = computed<GateItem | undefined>(() => gates.items.get(currentId.value ?? '') ?? queue.value[0])

watch(
  () => current.value?.stepId,
  (id) => {
    if (id && route.query.step !== id) void router.replace({ query: { ...route.query, step: id } })
  }
)

// Remember the queue position before resolving, so the next item slides into the same slot.
let lastIndex = 0
watch(
  () => current.value?.stepId,
  (id) => {
    if (currentId.value && id !== currentId.value) return
    const i = queue.value.findIndex((x) => x.stepId === id)
    if (i >= 0) lastIndex = i
  },
  { immediate: true }
)
function onResolved(): void {
  const next = queue.value[Math.min(lastIndex, queue.value.length - 1)]
  currentId.value = next?.stepId ?? null
}

function leadBlob(g: GateItem): BlobRef | undefined {
  return g.candidates.find(isImage) ?? g.candidates[0]
}

function runTitle(g: GateItem): string {
  return runs.runs.get(g.runId)?.title || 'Lượt chạy'
}

function instruction(g: GateItem): string {
  if (g.kind === 'approve') return 'Xem kết quả rồi duyệt hoặc từ chối'
  const need = g.k > 0 ? Math.min(g.k, g.candidates.length) : 0
  return need ? `Chọn ${need} ảnh để chạy tiếp` : 'Chọn ít nhất 1 ảnh để chạy tiếp'
}
</script>

<template>
  <div class="view">
    <PageHeader title="Cần duyệt" :subtitle="gates.count ? `${gates.count} bước đang chờ` : 'Không có gì đang chờ'" />
    <EmptyState v-if="!current" class="gates-empty" :art="ART.emptyGates" :art-size="176" title="Hộp duyệt trống" text="Khi một lượt chạy cần bạn chọn ảnh hoặc duyệt, nó sẽ xuất hiện ở đây." />
    <div v-else class="split">
      <aside class="queue" aria-label="Danh sách chờ duyệt" :style="{ '--pane-w': `${queueW}px` }">
        <TransitionGroup name="q" tag="div" class="q-list">
          <button
            v-for="g in queue"
            :key="g.stepId"
            class="q-item"
            :class="{ on: g.stepId === current.stepId }"
            :aria-current="g.stepId === current.stepId"
            @click="currentId = g.stepId"
          >
            <span class="q-thumb">
              <BlobThumb v-if="leadBlob(g)" :blob="leadBlob(g)!" :size="96" />
            </span>
            <span class="q-text">
              <span class="q-title">{{ runTitle(g) }}</span>
              <span class="caption q-meta">
                {{ g.kind === 'pick' ? 'Chọn ảnh' : 'Duyệt' }} · <span class="num">{{ g.candidates.length }}</span> {{ g.kind === 'pick' ? 'ảnh' : 'tệp' }} · <RelTime :ts="g.since" />
              </span>
            </span>
          </button>
        </TransitionGroup>
      </aside>
      <ResizeHandle v-model="queueW" label="Danh sách chờ duyệt" :default-size="280" :min="220" :max="440" />

      <section class="stage">
        <Transition name="swap" mode="out-in">
          <div :key="current.stepId" class="stage-inner">
            <header class="stage-head">
              <div>
                <h2 class="title">{{ runTitle(current) }}</h2>
                <p class="secondary">{{ instruction(current) }}</p>
              </div>
              <RouterLink :to="`/runs/${current.runId}`" class="btn btn-plain"><ExternalLink /> Mở lượt chạy</RouterLink>
            </header>
            <GatePicker v-if="current.kind === 'pick'" :gate="current" @resolved="onResolved" />
            <GateApprove v-else :gate="current" @resolved="onResolved" />
          </div>
        </Transition>
      </section>
    </div>
  </div>
</template>

<style scoped>
.gates-empty {
  flex: 1;
  align-content: center;
  padding-bottom: 72px;
}
.split {
  flex: 1;
  min-height: 0;
  display: flex;
}
.queue {
  width: var(--pane-w, 280px);
  max-width: 45%;
  flex: none;
  overflow: auto;
  padding: 12px 10px;
  border-right: 0.5px solid var(--separator);
}
.q-list {
  position: relative;
  display: grid;
  gap: 4px;
}
.q-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 56px;
  text-align: left;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.q-item:hover {
  background: var(--bg-hover);
}
.q-item.on {
  background: var(--bg-selected);
}
.q-thumb {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.q-thumb :deep(.blob),
.q-thumb :deep(.glyph) {
  width: 40px;
  height: 40px;
  padding: 0;
  gap: 0;
}
.q-thumb :deep(.glyph .tag) {
  display: none;
}
.q-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.q-meta {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.q-title {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.stage-inner {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.stage-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 8px;
}
.swap-enter-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.swap-leave-active {
  transition: opacity 100ms var(--ease-in);
}
.swap-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.swap-leave-to {
  opacity: 0;
}
.q-move,
.q-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-base);
}
.q-leave-active {
  transition:
    transform var(--t-fast) var(--ease-in),
    opacity var(--t-fast);
  position: absolute;
  width: 100%;
}
.q-enter-from {
  opacity: 0;
  transform: translateX(-8px);
}
.q-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}
</style>
