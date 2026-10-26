<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { ArrowRight, ChevronRight, Plus, Search } from '@lucide/vue'
import type { ApiError } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import ResourceCard from '@/components/ResourceCard.vue'
import RunRowItem from '@/components/RunRowItem.vue'
import BlobImage from '@/components/BlobImage.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import RelTime from '@/components/RelTime.vue'
import GettingStarted from '@/components/GettingStarted.vue'
import AgentConnectCard from '@/components/AgentConnectCard.vue'
import DeviceCard from '@/components/DeviceCard.vue'
import { useResources } from '@/stores/resources'
import { useRuns } from '@/stores/runs'
import { useGates, type GateItem } from '@/stores/gates'
import { useRecipes } from '@/stores/recipes'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { stepName } from '@/composables/format'
import { openPalette } from '@/composables/palette'
import { rememberOrigin } from '@/composables/motion'

const resources = useResources()
const runs = useRuns()
const gates = useGates()
const recipes = useRecipes()
const conn = useConnection()
const toasts = useToasts()

watch(
  () => conn.transport,
  (t) => t && void recipes.load(),
  { immediate: true }
)

const firstTime = computed(() => runs.listLoaded && !runs.listError && runs.runList.length === 0)
const needsYou = computed(() => gates.list.slice(0, 5))
const running = computed(() => runs.runningRuns.slice(0, 6))
const recent = computed(() => runs.runList.filter((r) => r.status === 'succeeded' && runs.previewHash(r.id)).slice(0, 12))

function runTitle(runId: string): string {
  const r = runs.runs.get(runId)
  if (!r) return 'Lượt chạy'
  return r.title || recipes.byId(r.recipe_id)?.name || 'Lượt chạy'
}

function gateAsk(g: GateItem): string {
  if (g.kind === 'pick') {
    const n = g.candidates.length
    if (g.k === 1 && n) return `Chọn 1 trong ${n} ảnh`
    return n ? `Chọn ảnh ưng ý trong ${n} ảnh` : 'Chọn ảnh'
  }
  const name = g.step ? stepName(g.step) : ''
  return name && name !== 'Duyệt kết quả' ? `Duyệt: ${name}` : 'Duyệt kết quả'
}

// Approve gates often carry JSON/atlas next to the image; the row thumbnail should be the picture.
function gateThumb(g: GateItem) {
  return g.candidates.find((b) => b.mime.startsWith('image/'))
}

// Running rows need their steps for the inline pipeline dots and live preview. Hold each only while shown so
// resync reloads just these; WS keeps them fresh in between.
const HOLDER = 'dashboard'
const held = new Set<string>()
watch(
  [running, () => conn.transport],
  ([list, transport]) => {
    const ids = new Set(list.map((r) => r.id))
    for (const id of held) {
      if (ids.has(id)) continue
      held.delete(id)
      runs.closeDetail(id, HOLDER)
    }
    if (!transport) return
    for (const id of ids) {
      if (held.has(id)) continue
      held.add(id)
      runs.loadDetail(id, HOLDER).catch(() => {
        held.delete(id)
        runs.closeDetail(id, HOLDER)
      })
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  for (const id of held) runs.closeDetail(id, HOLDER)
  held.clear()
})

const RESOURCE_LABEL: Record<string, string> = { comfyui: 'ComfyUI', blender: 'Blender', codex: 'Codex', claude: 'Claude' }

async function toggle(name: string, start: boolean): Promise<void> {
  try {
    await resources.toggle(name, start)
  } catch (e) {
    const err = e as ApiError
    if (start || err.status !== 409) return toasts.error(e, start ? 'Không bật được' : 'Không tắt được')
    const label = RESOURCE_LABEL[name] ?? name
    const ask =
      err.detail?.status === 'starting'
        ? `${label} đang khởi động, dừng ngay?
Lần khởi động này sẽ bị huỷ.`
        : `${label} đang chạy job, dừng ngay?
Bước đang chạy sẽ bị ngắt và phải chạy lại.`
    if (!window.confirm(ask)) return
    try {
      await resources.toggle(name, false, true)
    } catch (e2) {
      toasts.error(e2, 'Không tắt được')
    }
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Tổng quan">
      <button class="btn search-btn" title="Tìm nhanh (Ctrl+K)" @click="openPalette">
        <Search /> Tìm… <span class="kbd">Ctrl K</span>
      </button>
      <RouterLink to="/new" class="btn btn-primary" title="Tạo mới (Ctrl+N)"><Plus /> Tạo mới</RouterLink>
    </PageHeader>
    <div class="scroll">
      <div class="stack dash">
        <section class="strip card" aria-label="Tài nguyên">
          <template v-if="resources.list.length">
            <template v-for="(r, i) in resources.list" :key="r.name">
              <span v-if="i" class="sep" aria-hidden="true" />
              <ResourceCard :resource="r" :pending="resources.pending.has(r.name)" @toggle="toggle(r.name, $event)" />
            </template>
          </template>
          <span v-else class="skeleton" />
        </section>

        <DeviceCard />

        <GettingStarted v-if="firstTime" />

        <template v-else>
          <Transition name="section">
            <section v-if="gates.count" class="sec" aria-labelledby="sec-you">
              <div class="sec-head">
                <h2 id="sec-you" class="section-label">
                  Cần bạn <span class="num count human">{{ gates.count }}</span>
                </h2>
                <RouterLink to="/gates" class="link">Duyệt lần lượt <ArrowRight :size="14" /></RouterLink>
              </div>
              <div v-auto-animate class="list card">
                <RouterLink v-for="g in needsYou" :key="g.stepId" :to="{ path: '/gates', query: { step: g.stepId } }" class="gate-row">
                  <span class="g-thumb">
                    <BlobImage v-if="gateThumb(g)" :hash="gateThumb(g)!.hash" :size="96" />
                    <RecipeThumb v-else-if="runs.runs.get(g.runId)" :id="runs.runs.get(g.runId)!.recipe_id" :size="40" />
                  </span>
                  <span class="g-text">
                    <span class="headline g-title">{{ runTitle(g.runId) }}</span>
                    <span class="callout ask">{{ gateAsk(g) }}</span>
                  </span>
                  <RelTime :ts="g.since" class="caption" />
                  <ChevronRight :size="16" class="chev" />
                </RouterLink>
                <RouterLink v-if="gates.count > needsYou.length" to="/gates" class="more caption">
                  và <span class="num">{{ gates.count - needsYou.length }}</span> mục khác
                </RouterLink>
              </div>
            </section>
          </Transition>

          <section class="sec" aria-labelledby="sec-run">
            <div class="sec-head">
              <h2 id="sec-run" class="section-label">
                Đang chạy <span v-if="runs.runningRuns.length" class="num count">{{ runs.runningRuns.length }}</span>
              </h2>
            </div>
            <div v-if="running.length" v-auto-animate class="list card">
              <div v-for="r in running" :key="r.id" class="list-row">
                <RunRowItem :run="r" :steps="runs.stepsOf(r.id)" />
              </div>
            </div>
            <p v-else class="idle callout">Không có lượt nào đang chạy.</p>
          </section>

          <section class="sec" aria-labelledby="sec-recent">
            <div class="sec-head">
              <h2 id="sec-recent" class="section-label">Gần đây</h2>
              <RouterLink to="/runs" class="link">Xem tất cả <ChevronRight :size="14" /></RouterLink>
            </div>
            <div v-if="recent.length" class="tiles">
              <RouterLink v-for="(r, i) in recent" :key="r.id" :to="`/runs/${r.id}`" class="tile" :style="{ '--i': i }" :title="runTitle(r.id)"
                @click="rememberOrigin(r.id, ($event.currentTarget as HTMLElement).querySelector('.t-img'))">
                <span class="t-img"><BlobImage :hash="runs.previewHash(r.id)!" :size="256" /></span>
                <span class="t-name">{{ runTitle(r.id) }}</span>
                <RelTime :ts="r.ended_at ?? r.created_at" class="caption" />
              </RouterLink>
            </div>
            <p v-else class="idle callout">
              Asset hoàn tất sẽ hiện ở đây.
              <RouterLink to="/new" class="link inline">Tạo asset mới</RouterLink>
            </p>
          </section>
        </template>

        <AgentConnectCard />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dash {
  gap: var(--sp-8);
}
.search-btn {
  color: var(--text-2);
  gap: 8px;
}
.search-btn .kbd {
  margin-left: 4px;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 16px;
  min-height: 48px;
  padding: 8px 16px;
}
.sep {
  width: 0.5px;
  height: 18px;
  background: var(--separator-strong);
}
.skeleton {
  flex: 1;
  height: 20px;
  border-radius: 6px;
  position: relative;
  overflow: hidden;
  background: var(--bg-sunken);
}
.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, transparent 20%, var(--bg-hover) 50%, transparent 80%);
  animation: shimmer 1.4s ease-in-out infinite;
}
.sec {
  display: grid;
  gap: 10px;
}
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sec-head .section-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.count {
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 9px;
  display: inline-grid;
  place-items: center;
  font-size: var(--fs-caption);
  color: var(--text-2);
  background: var(--bg-active);
}
.count.human {
  color: var(--bg-content);
  background: var(--st-human);
}
.link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
  font-size: var(--fs-callout);
}
.link.inline {
  display: inline;
  margin-left: 6px;
}
.list {
  padding: 4px;
  display: grid;
}
.list-row {
  height: 58px;
}
.list-row + .list-row,
.gate-row + .gate-row {
  border-top: 0.5px solid var(--separator);
}
.gate-row {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 58px;
  padding: 0 14px 0 8px;
  color: inherit;
  text-decoration: none;
  border-radius: var(--r-md);
  transition: background-color var(--t-fast);
}
.gate-row:hover {
  background: var(--bg-hover);
}
.g-thumb {
  flex: none;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: var(--hairline);
}
.g-thumb :deep(.blob) {
  width: 100%;
  height: 100%;
}
.g-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 1px;
}
.g-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ask {
  color: var(--st-human);
  font-weight: 500;
}
.chev {
  flex: none;
  color: var(--text-3);
}
.more {
  padding: 8px 14px;
  text-decoration: none;
  border-top: 0.5px solid var(--separator);
}
.idle {
  padding: 2px 2px 0;
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(124px, 1fr));
  gap: 14px;
}
.tile {
  display: grid;
  gap: 4px;
  min-width: 0;
  color: inherit;
  text-decoration: none;
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
  animation-delay: calc(min(var(--i, 0), 12) * 30ms);
}
.t-img {
  display: block;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--shadow-1), var(--hairline);
  transition:
    transform var(--t-fast) var(--ease-spring),
    box-shadow var(--t-base) var(--ease-out);
}
.tile:hover .t-img {
  box-shadow: var(--shadow-2), var(--hairline);
}
.tile:active .t-img {
  transform: scale(0.97);
}
.t-name {
  margin-top: 2px;
  font-size: var(--fs-callout);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.section-enter-active {
  transition:
    transform var(--t-slow) var(--ease-spring),
    opacity var(--t-base);
}
.section-leave-active {
  transition: opacity var(--t-fast);
}
.section-enter-from {
  transform: translateY(-6px);
  opacity: 0;
}
.section-leave-to {
  opacity: 0;
}
</style>
