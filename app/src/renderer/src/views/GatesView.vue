<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useElementSize, onKeyStroke } from '@vueuse/core'
import { ART } from '@/assets/art'
import { Check, ExternalLink, LoaderCircle, ThumbsDown, ThumbsUp } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import Lightbox from '@/components/Lightbox.vue'
import AssetViewer from '@/components/viewers/AssetViewer.vue'
import { featuredAsset } from '@/components/viewers/detect'
import type { BlobRef } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import { useGates, type GateItem } from '@/stores/gates'
import { useRuns } from '@/stores/runs'
import { useToasts } from '@/stores/toasts'
import RelTime from '@/components/RelTime.vue'

const gates = useGates()
const runs = useRuns()
const toasts = useToasts()
const route = useRoute()
const router = useRouter()

const currentId = ref<string | null>((route.query.step as string | undefined) ?? null)
const current = computed<GateItem | undefined>(() => gates.items.get(currentId.value ?? '') ?? gates.list[0])

watch(
  () => current.value?.stepId,
  (id, prev) => {
    if (id === prev) return
    picked.value = new Set()
    focus.value = 0
    if (id && route.query.step !== id) void router.replace({ query: { step: id } })
  }
)

const picked = ref(new Set<string>())
const focus = ref(0)
const sending = ref(false)

const candidates = computed(() => current.value?.candidates ?? [])
const need = computed(() => {
  const g = current.value
  if (!g || g.kind !== 'pick') return 0
  return g.k > 0 ? Math.min(g.k, candidates.value.length) : 0
})
const valid = computed(() => (need.value ? picked.value.size === need.value : picked.value.size >= 1))
const instruction = computed(() => {
  const g = current.value
  if (!g) return ''
  if (g.kind === 'approve') return 'Xem kết quả rồi duyệt hoặc từ chối'
  return need.value ? `Chọn ${need.value} ảnh để chạy tiếp` : 'Chọn ít nhất 1 ảnh để chạy tiếp'
})

function runTitle(g: GateItem): string {
  return runs.runs.get(g.runId)?.title || 'Run'
}

const featured = computed(() => (current.value?.kind === 'approve' ? featuredAsset(candidates.value) : null))
const lightbox = ref<BlobRef | null>(null)

function toggle(i: number): void {
  const b = candidates.value[i]
  if (b && current.value?.kind === 'approve') {
    focus.value = i
    lightbox.value = b
    return
  }
  if (!b || current.value?.kind !== 'pick') return
  focus.value = i
  const next = new Set(picked.value)
  if (next.has(b.hash)) next.delete(b.hash)
  else {
    if (need.value === 1) next.clear()
    else if (need.value && next.size >= need.value) return
    next.add(b.hash)
  }
  picked.value = next
}

async function resolve(approved?: boolean): Promise<void> {
  const g = current.value
  if (!g || sending.value) return
  if (g.kind === 'pick' && !valid.value) return
  sending.value = true
  const idx = gates.list.findIndex((x) => x.stepId === g.stepId)
  try {
    await gates.resolve(g.stepId, g.kind === 'pick' ? { picked: [...picked.value] } : { approved: approved ?? true })
    toasts.push(g.kind === 'pick' ? 'Đã gửi lựa chọn, run chạy tiếp' : approved === false ? 'Đã từ chối' : 'Đã duyệt', 'success', 2500)
    const next = gates.list[Math.min(idx, gates.list.length - 1)]
    currentId.value = next?.stepId ?? null
  } catch (e) {
    toasts.error(e, 'Không gửi được')
  } finally {
    sending.value = false
  }
}

const grid = ref<HTMLElement | null>(null)
const { width } = useElementSize(grid)
const TILE_MIN = 180
const GAP = 14
const cols = computed(() => Math.max(1, Math.floor((width.value + GAP) / (TILE_MIN + GAP))))

function inField(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')
}

onKeyStroke(true, (e) => {
  if (!current.value || lightbox.value || inField(e) || e.ctrlKey || e.metaKey || e.altKey) return
  const n = candidates.value.length
  if (/^[1-9]$/.test(e.key)) {
    const i = Number(e.key) - 1
    if (i < n) {
      e.preventDefault()
      toggle(i)
    }
    return
  }
  const move: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols.value, ArrowUp: -cols.value }
  if (e.key in move && n) {
    e.preventDefault()
    focus.value = Math.min(n - 1, Math.max(0, focus.value + move[e.key]!))
    return
  }
  if (e.key === ' ' && current.value.kind === 'pick') {
    e.preventDefault()
    toggle(focus.value)
    return
  }
  if (e.key === 'Enter') {
    e.preventDefault()
    void resolve(true)
    return
  }
  if ((e.key === 'x' || e.key === 'X') && current.value.kind === 'approve') {
    e.preventDefault()
    void resolve(false)
  }
})
</script>

<template>
  <div class="view">
    <PageHeader title="Cần duyệt" :subtitle="gates.count ? `${gates.count} bước đang chờ` : 'Không có gì đang chờ'" />
    <EmptyState v-if="!current" class="gates-empty" :art="ART.emptyGates" :art-size="176" title="Hộp duyệt trống" text="Khi một run cần bạn chọn ảnh hoặc duyệt, nó sẽ xuất hiện ở đây." />
    <div v-else class="split">
      <aside class="queue" aria-label="Danh sách chờ duyệt">
        <TransitionGroup name="q" tag="div" class="q-list">
          <button
            v-for="g in gates.list"
            :key="g.stepId"
            class="q-item"
            :class="{ on: g.stepId === current.stepId }"
            :aria-current="g.stepId === current.stepId"
            @click="currentId = g.stepId"
          >
            <span class="q-title">{{ runTitle(g) }}</span>
            <span class="caption">
              {{ g.kind === 'pick' ? 'Chọn ảnh' : 'Duyệt' }} · {{ g.candidates.length }} ảnh · <RelTime :ts="g.since" />
            </span>
          </button>
        </TransitionGroup>
      </aside>

      <section class="stage">
        <Transition name="swap" mode="out-in">
          <div :key="current.stepId" class="stage-inner">
            <header class="stage-head">
              <div>
                <h2 class="title">{{ runTitle(current) }}</h2>
                <p class="secondary">{{ instruction }}</p>
              </div>
              <RouterLink :to="`/runs/${current.runId}`" class="btn btn-plain"><ExternalLink /> Xem run</RouterLink>
            </header>

            <div v-if="featured" class="feature card">
              <AssetViewer :key="featured.blob.hash" :view="featured" />
            </div>

            <div ref="grid" class="sheet" :class="{ strip: featured }" role="listbox" :aria-multiselectable="need !== 1" :aria-label="instruction">
              <button
                v-for="(b, i) in candidates"
                :key="b.hash"
                class="tile"
                role="option"
                :aria-selected="picked.has(b.hash)"
                :class="{ picked: picked.has(b.hash), focused: focus === i, readonly: current.kind !== 'pick' }"
                :style="{ animationDelay: `${Math.min(i, 8) * 35}ms` }"
                @click="toggle(i)"
                @dblclick="need === 1 && (toggle(i), resolve())"
              >
                <BlobThumb :blob="b" :size="featured ? 192 : 384" />
                <span v-if="i < 9" class="kbd idx">{{ i + 1 }}</span>
                <Transition name="pop">
                  <span v-if="picked.has(b.hash)" class="tick"><Check :size="14" :stroke-width="3" /></span>
                </Transition>
              </button>
            </div>

            <footer class="actions">
              <span class="caption hints">
                <span class="kbd">1</span>–<span class="kbd">9</span> chọn · <span class="kbd">←</span><span class="kbd">→</span> di chuyển ·
                <span class="kbd">Enter</span> {{ current.kind === 'pick' ? 'xác nhận' : 'duyệt' }}
                <template v-if="current.kind === 'approve'"> · <span class="kbd">X</span> từ chối</template>
              </span>
              <template v-if="current.kind === 'pick'">
                <span class="num secondary">{{ picked.size }}{{ need ? ` / ${need}` : '' }} đã chọn</span>
                <button class="btn btn-primary btn-lg" :disabled="!valid || sending" @click="resolve()">
                  <LoaderCircle v-if="sending" class="spin" />
                  <Check v-else />
                  Dùng ảnh đã chọn
                </button>
              </template>
              <template v-else>
                <button class="btn btn-lg btn-danger" :disabled="sending" @click="resolve(false)"><ThumbsDown /> Từ chối</button>
                <button class="btn btn-primary btn-lg" :disabled="sending" @click="resolve(true)"><ThumbsUp /> Duyệt</button>
              </template>
            </footer>
          </div>
        </Transition>
      </section>
    </div>
    <Lightbox :blob="lightbox" :siblings="candidates" @close="lightbox = null" />
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
  width: 260px;
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
  display: grid;
  gap: 2px;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
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
.sheet {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px 24px 24px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 14px;
  align-content: start;
}
.feature {
  flex: 1 1 auto;
  min-height: 300px;
  margin: 4px 24px 0;
  overflow: hidden;
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
}
.sheet.strip {
  flex: 0 0 auto;
  max-height: 200px;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
  gap: 10px;
}
.tile {
  position: relative;
  padding: 0;
  border: 0;
  border-radius: var(--r-lg);
  overflow: hidden;
  background: none;
  cursor: pointer;
  box-shadow: var(--shadow-1), var(--hairline);
  transition:
    transform var(--t-base) var(--ease-spring),
    box-shadow var(--t-fast) var(--ease-out);
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
}
.tile:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2), var(--hairline);
}
.tile.focused {
  box-shadow: 0 0 0 2px var(--focus-ring), var(--shadow-2);
}
.tile.picked {
  transform: scale(0.97);
  box-shadow: 0 0 0 3px var(--accent), var(--shadow-2);
}
.tile.readonly {
  cursor: zoom-in;
}
.idx {
  position: absolute;
  top: 8px;
  left: 8px;
  background: color-mix(in srgb, var(--bg-elevated) 85%, transparent);
  backdrop-filter: blur(8px);
}
.tick {
  position: absolute;
  top: 8px;
  right: 8px;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  color: var(--text-on-accent);
  background: var(--accent);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.actions {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  padding: 12px 24px;
  border-top: 0.5px solid var(--separator);
  background: color-mix(in srgb, var(--bg-content) 85%, transparent);
  backdrop-filter: blur(16px);
}
.hints {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.spin {
  animation: spin 1s linear infinite;
}
.pop-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast);
}
.pop-leave-active {
  transition: opacity 100ms;
}
.pop-enter-from {
  transform: scale(0.3);
  opacity: 0;
}
.pop-leave-to {
  opacity: 0;
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
