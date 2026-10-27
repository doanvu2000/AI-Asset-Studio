<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, ExternalLink, LoaderCircle, ThumbsDown, ThumbsUp } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import BlobThumb from '@/components/BlobThumb.vue'
import Lightbox from '@/components/Lightbox.vue'
import { featuredAsset, isImage } from '@/components/viewers/detect'
import { useGates, type GateItem } from '@/stores/gates'
import { useRuns } from '@/stores/runs'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ gates: GateItem[] }>()

const store = useGates()
const runs = useRuns()
const toasts = useToasts()

const checked = ref(new Set<string>())
const picks = ref(new Map<string, string[]>())
const sending = ref<'yes' | 'no' | null>(null)
const lightbox = ref<{ blob: BlobRef; siblings: BlobRef[] } | null>(null)

watch(
  () => props.gates.map((g) => g.stepId),
  (ids) => {
    const live = new Set(ids)
    checked.value = new Set([...checked.value].filter((id) => live.has(id)))
    picks.value = new Map([...picks.value].filter(([id]) => live.has(id)))
  }
)

function need(g: GateItem): number {
  return g.k > 0 ? Math.min(g.k, g.candidates.length) : 1
}
function picked(g: GateItem): string[] {
  return picks.value.get(g.stepId) ?? []
}
function ready(g: GateItem): boolean {
  return g.kind === 'approve' || picked(g).length === need(g)
}

function setChecked(id: string, on: boolean): void {
  const next = new Set(checked.value)
  if (on) next.add(id)
  else next.delete(id)
  checked.value = next
}

function togglePick(g: GateItem, b: BlobRef): void {
  const cur = picked(g)
  let next: string[]
  if (cur.includes(b.hash)) next = cur.filter((h) => h !== b.hash)
  else if (need(g) === 1) next = [b.hash]
  else if (cur.length >= need(g)) return
  else next = [...cur, b.hash]
  picks.value = new Map(picks.value).set(g.stepId, next)
  setChecked(g.stepId, next.length === need(g))
}

function fillFirst(): void {
  const next = new Map(picks.value)
  const on = new Set(checked.value)
  for (const g of props.gates) {
    if (g.kind !== 'pick' || picked(g).length === need(g)) continue
    next.set(g.stepId, g.candidates.slice(0, need(g)).map((b) => b.hash))
    on.add(g.stepId)
  }
  picks.value = next
  checked.value = on
}

const allChecked = computed(() => props.gates.length > 0 && props.gates.every((g) => checked.value.has(g.stepId)))
function toggleAll(): void {
  checked.value = allChecked.value ? new Set() : new Set(props.gates.map((g) => g.stepId))
}

const selected = computed(() => props.gates.filter((g) => checked.value.has(g.stepId)))
const toApprove = computed(() => selected.value.filter(ready))
const toReject = computed(() => selected.value.filter((g) => g.kind === 'approve'))
const missing = computed(() => selected.value.length - toApprove.value.length)

async function send(approve: boolean): Promise<void> {
  const list = approve ? toApprove.value : toReject.value
  if (!list.length || sending.value) return
  sending.value = approve ? 'yes' : 'no'
  // One at a time: each resolve schedules the run's next GPU step, and the core queues them in this order.
  let failed = 0
  for (const g of list) {
    try {
      await store.resolve(g.stepId, g.kind === 'pick' ? { picked: picked(g) } : { approved: approve })
    } catch {
      failed++
    }
  }
  sending.value = null
  const done = list.length - failed
  if (done) toasts.push(approve ? `Đã gửi ${done} mục` : `Đã từ chối ${done} mục`, approve ? 'success' : 'info', 3000)
  if (failed) toasts.push(`${failed} mục không gửi được`, 'error', 4000)
}

function title(g: GateItem): string {
  return runs.runs.get(g.runId)?.title || 'Lượt chạy'
}
function cover(g: GateItem): BlobRef | undefined {
  return featuredAsset(g.candidates)?.blob ?? g.candidates.find(isImage)
}
</script>

<template>
  <div class="ov">
    <div class="grid">
      <article
        v-for="g in gates"
        :key="g.stepId"
        class="card gcard"
        :class="{ on: checked.has(g.stepId), wait: checked.has(g.stepId) && !ready(g) }"
      >
        <header class="ghead">
          <button
            type="button"
            class="tick"
            role="checkbox"
            :aria-checked="checked.has(g.stepId)"
            :aria-label="`Chọn ${title(g)}`"
            @click="setChecked(g.stepId, !checked.has(g.stepId))"
          >
            <Check v-if="checked.has(g.stepId)" :size="13" :stroke-width="3" />
          </button>
          <span class="gtitle" :title="title(g)">{{ title(g) }}</span>
          <span class="caption kind">{{ g.kind === 'pick' ? `Chọn ${need(g)} ảnh` : 'Duyệt' }}</span>
          <RouterLink :to="`/runs/${g.runId}`" class="open" title="Mở lượt chạy"><ExternalLink :size="14" /></RouterLink>
        </header>
        <div v-if="g.kind === 'pick'" class="cands" :style="{ '--n': Math.min(g.candidates.length, 4) }">
          <button
            v-for="(b, i) in g.candidates"
            :key="b.hash"
            type="button"
            class="cand"
            :class="{ picked: picked(g).includes(b.hash) }"
            :aria-pressed="picked(g).includes(b.hash)"
            :aria-label="`Ảnh ${i + 1}`"
            @click="togglePick(g, b)"
            @dblclick="lightbox = { blob: b, siblings: g.candidates }"
          >
            <BlobThumb :blob="b" :size="192" />
            <span v-if="picked(g).includes(b.hash)" class="mark"><Check :size="12" :stroke-width="3" /></span>
          </button>
        </div>
        <button
          v-else-if="cover(g)"
          type="button"
          class="cover"
          aria-label="Xem kết quả"
          @click="lightbox = { blob: cover(g)!, siblings: g.candidates }"
        >
          <BlobThumb :blob="cover(g)!" :size="384" />
        </button>
      </article>
    </div>
    <footer class="actions">
      <button class="btn" @click="toggleAll">{{ allChecked ? 'Bỏ chọn tất cả' : 'Chọn tất cả' }}</button>
      <button class="btn" title="Lượt chọn ảnh nào chưa chọn thì lấy ảnh đầu" @click="fillFirst">Lấy ảnh đầu</button>
      <span class="spacer" />
      <span v-if="missing" class="caption warn">{{ missing }} mục chưa chọn ảnh</span>
      <button class="btn" :disabled="!toReject.length || !!sending" @click="send(false)">
        <LoaderCircle v-if="sending === 'no'" class="spin" />
        <ThumbsDown v-else />
        Từ chối ({{ toReject.length }})
      </button>
      <button class="btn btn-primary btn-lg" :disabled="!toApprove.length || !!sending" @click="send(true)">
        <LoaderCircle v-if="sending === 'yes'" class="spin" />
        <ThumbsUp v-else />
        Duyệt ({{ toApprove.length }})
      </button>
    </footer>
    <Lightbox :blob="lightbox?.blob ?? null" :siblings="lightbox?.siblings ?? []" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.ov {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.grid {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px 24px 24px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
  align-content: start;
  align-items: start;
}
.gcard {
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 10px;
  border-radius: var(--r-lg);
  transition: box-shadow var(--t-fast);
}
.gcard.on {
  box-shadow: 0 0 0 2px var(--accent), var(--shadow-1);
}
.gcard.wait {
  box-shadow: 0 0 0 2px var(--st-waiting), var(--shadow-1);
}
.ghead {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.tick {
  flex: none;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--text-on-accent);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  cursor: pointer;
}
.gcard.on .tick {
  background: var(--accent);
}
.gtitle {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kind {
  flex: none;
  color: var(--text-3);
}
.open {
  flex: none;
  display: grid;
  place-items: center;
  color: var(--text-3);
}
.open:hover {
  color: var(--text);
}
.cands {
  display: grid;
  grid-template-columns: repeat(var(--n), 1fr);
  gap: 6px;
}
.cand,
.cover {
  position: relative;
  padding: 0;
  border: 0;
  border-radius: var(--r-md);
  overflow: hidden;
  background: var(--bg-sunken);
  cursor: pointer;
  box-shadow: var(--hairline);
}
.cand {
  aspect-ratio: 1;
}
.cover {
  aspect-ratio: 4 / 3;
}
.cand :deep(.blob),
.cand :deep(.glyph),
.cover :deep(.blob),
.cover :deep(.glyph) {
  width: 100%;
  height: 100%;
}
.cand.picked {
  box-shadow: 0 0 0 3px var(--accent);
}
.cover {
  cursor: zoom-in;
}
.mark {
  position: absolute;
  top: 4px;
  right: 4px;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  color: var(--text-on-accent);
  background: var(--accent);
}
.actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 24px;
  border-top: 0.5px solid var(--separator);
  background: color-mix(in srgb, var(--bg-content) 85%, transparent);
  backdrop-filter: blur(16px);
}
.spacer {
  flex: 1;
}
.warn {
  color: var(--st-waiting);
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
