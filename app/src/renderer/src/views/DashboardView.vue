<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { Inbox, Plus } from '@lucide/vue'
import { ART } from '@/assets/art'
import PageHeader from '@/components/PageHeader.vue'
import ResourceCard from '@/components/ResourceCard.vue'
import RunRowItem from '@/components/RunRowItem.vue'
import EmptyState from '@/components/EmptyState.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useResources } from '@/stores/resources'
import { useRuns } from '@/stores/runs'
import { useGates } from '@/stores/gates'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const resources = useResources()
const runs = useRuns()
const gates = useGates()
const conn = useConnection()
const toasts = useToasts()

const active = computed(() => runs.activeRuns.slice(0, 6))
const recent = computed(() => runs.runList.filter((r) => r.status !== 'running' && r.status !== 'awaiting_human').slice(0, 8))

// Active runs need their steps for the inline pipeline dots. Hold each only while it is shown so
// resync reloads just these; WS keeps them fresh in between.
const HOLDER = 'dashboard'
const held = new Set<string>()
watch(
  [active, () => conn.transport],
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

async function toggle(name: string, start: boolean): Promise<void> {
  try {
    await resources.toggle(name, start)
  } catch (e) {
    toasts.error(e, start ? 'Không bật được' : 'Không tắt được')
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="Tổng quan">
      <RouterLink to="/new" class="btn btn-primary"><Plus /> Tạo mới</RouterLink>
    </PageHeader>
    <div class="scroll">
      <div class="stack">
        <section>
          <h2 class="section-label">Tài nguyên</h2>
          <div v-if="resources.list.length" class="res-grid">
            <ResourceCard
              v-for="r in resources.list"
              :key="r.name"
              :resource="r"
              :pending="resources.pending.has(r.name)"
              @toggle="toggle(r.name, $event)"
            />
          </div>
          <div v-else class="res-grid">
            <div v-for="i in 4" :key="i" class="card skeleton" />
          </div>
        </section>

        <Transition name="callout">
          <RouterLink v-if="gates.count" to="/gates" class="callout card">
            <span class="callout-ic"><Inbox :size="18" /></span>
            <span class="callout-text">
              <strong><TweenNumber :value="gates.count" /> bước đang chờ bạn duyệt</strong>
              <span class="caption">Run sẽ dừng ở đó cho tới khi bạn chọn.</span>
            </span>
            <span class="btn">Mở hộp duyệt</span>
          </RouterLink>
        </Transition>

        <section>
          <div class="sec-head">
            <h2 class="section-label">Đang chạy</h2>
            <span class="caption num">{{ runs.activeRuns.length }}</span>
          </div>
          <div v-if="active.length" v-auto-animate class="list card">
            <div v-for="r in active" :key="r.id" class="list-row">
              <RunRowItem :run="r" :steps="runs.stepsOf(r.id)" />
            </div>
          </div>
          <p v-else class="card idle secondary">Không có run nào đang chạy.</p>
        </section>

        <section>
          <div class="sec-head">
            <h2 class="section-label">Gần đây</h2>
            <RouterLink to="/runs" class="link caption">Xem tất cả</RouterLink>
          </div>
          <div v-if="recent.length" v-auto-animate class="list card">
            <div v-for="r in recent" :key="r.id" class="list-row">
              <RunRowItem :run="r" />
            </div>
          </div>
          <EmptyState v-else-if="runs.listLoaded" :art="ART.emptyRuns" title="Chưa có run nào" text="Chọn một recipe để tạo asset đầu tiên.">
            <RouterLink to="/new" class="btn btn-primary"><Plus /> Tạo mới</RouterLink>
          </EmptyState>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
section {
  display: grid;
  gap: 10px;
}
.sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.res-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.skeleton {
  height: 118px;
  position: relative;
  overflow: hidden;
}
.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, transparent 20%, var(--bg-hover) 50%, transparent 80%);
  animation: shimmer 1.4s ease-in-out infinite;
}
.list {
  padding: 4px;
  display: grid;
}
.list-row {
  height: 58px;
}
.list-row + .list-row {
  border-top: 0.5px solid var(--separator);
}
.idle {
  padding: 18px 16px;
}
.callout {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  color: inherit;
  text-decoration: none;
  background: color-mix(in srgb, var(--bg-elevated) 90%, var(--st-human));
}
.callout-ic {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  color: #fff;
  background: var(--st-human);
}
.callout-text {
  flex: 1;
  display: grid;
}
.link {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}
.callout-enter-active {
  transition:
    transform var(--t-slow) var(--ease-spring),
    opacity var(--t-base);
}
.callout-leave-active {
  transition: opacity var(--t-fast);
}
.callout-enter-from {
  transform: translateY(-6px) scale(0.98);
  opacity: 0;
}
.callout-leave-to {
  opacity: 0;
}
</style>
