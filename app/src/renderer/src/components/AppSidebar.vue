<script setup lang="ts">
import { ART } from '@/assets/art'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { motion } from 'motion-v'
import { Images, Inbox, LayoutDashboard, ListChecks, Plus } from '@lucide/vue'
import { useRuns } from '@/stores/runs'
import { useGates } from '@/stores/gates'
import { useVersions } from '@/stores/versions'
import { useConnection } from '@/stores/connection'
import TweenNumber from './TweenNumber.vue'

const route = useRoute()
const runs = useRuns()
const gates = useGates()
const versions = useVersions()
const conn = useConnection()

const items = computed(() => [
  { to: '/', name: 'dashboard', label: 'Tổng quan', icon: LayoutDashboard, badge: 0, tone: '' },
  { to: '/new', name: 'new', label: 'Tạo mới', icon: Plus, badge: 0, tone: '' },
  { to: '/runs', name: 'runs', label: 'Lượt chạy', icon: ListChecks, badge: runs.activeRuns.length, tone: 'run' },
  { to: '/gates', name: 'gates', label: 'Cần duyệt', icon: Inbox, badge: gates.count, tone: 'human' },
  { to: '/gallery', name: 'gallery', label: 'Thư viện', icon: Images, badge: versions.draftCount, tone: '' }
])

function isActive(name: string): boolean {
  const r = String(route.name ?? '')
  return r === name || (name === 'runs' && r === 'run')
}

const status = computed(() => {
  const c = conn.core
  if (c.mock) return { tone: 'mock', text: 'Chế độ mô phỏng' }
  if (conn.socket === 'auth_error') return { tone: 'bad', text: 'Sai token core' }
  if (c.state === 'ready' && conn.socket === 'open') return { tone: 'ok', text: 'Core đã kết nối' }
  if (c.state === 'ready') return { tone: 'warn', text: 'Đang nối lại…' }
  if (c.state === 'starting') return { tone: 'warn', text: 'Core đang khởi động…' }
  if (c.state === 'restarting') return { tone: 'warn', text: 'Core đang khởi động lại…' }
  return { tone: 'bad', text: 'Core không chạy' }
})
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <img class="logo" :src="ART.appIcon" alt="" width="22" height="22" draggable="false" />
      <span class="brand-name">Asset Studio</span>
    </div>

    <nav aria-label="Điều hướng chính">
      <RouterLink
        v-for="it in items"
        :key="it.to"
        :to="it.to"
        class="item"
        :class="{ active: isActive(it.name) }"
        :aria-current="isActive(it.name) ? 'page' : undefined"
      >
        <motion.span
          v-if="isActive(it.name)"
          layout-id="nav-pill"
          class="pill"
          :transition="{ type: 'spring', stiffness: 520, damping: 40 }"
        />
        <component :is="it.icon" class="ic" :size="16" :stroke-width="2" />
        <span class="label">{{ it.label }}</span>
        <Transition name="badge">
          <span v-if="it.badge > 0" class="count" :class="it.tone">
            <TweenNumber :value="it.badge" :duration="200" />
          </span>
        </Transition>
      </RouterLink>
    </nav>

    <div class="foot">
      <span class="conn" :class="status.tone">
        <span class="dot" />
        {{ status.text }}
      </span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  -webkit-app-region: drag;
  display: flex;
  flex-direction: column;
  width: var(--sidebar-w);
  flex: none;
  padding: 0 10px 12px;
  background: var(--bg-sidebar);
  border-right: 0.5px solid var(--separator);
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  height: var(--toolbar-h);
  padding: 0 8px;
}
.logo {
  width: 22px;
  height: 22px;
  filter: drop-shadow(0 1px 1.5px rgba(0, 0, 0, 0.22));
  user-select: none;
}
.brand-name {
  font-weight: 650;
  letter-spacing: -0.01em;
}
nav {
  -webkit-app-region: no-drag;
  display: grid;
  gap: 2px;
  margin-top: 6px;
}
.item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 10px;
  border-radius: 7px;
  color: var(--text);
  text-decoration: none;
  font-weight: 500;
  transition: background-color var(--t-fast);
}
.item:hover:not(.active) {
  background: var(--bg-hover);
}
.pill {
  position: absolute;
  inset: 0;
  border-radius: 7px;
  background: var(--bg-active);
  z-index: 0;
}
.ic,
.label,
.count {
  position: relative;
  z-index: 1;
}
.ic {
  color: var(--accent);
  flex: none;
}
.label {
  flex: 1;
}
.count {
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 650;
  color: var(--text-2);
  background: var(--bg-active);
}
.count.run {
  color: var(--st-running);
  background: var(--st-running-soft);
}
.count.human {
  color: #fff;
  background: var(--st-human);
}
.foot {
  margin-top: auto;
  padding: 0 6px;
}
.conn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--st-pending);
}
.ok .dot {
  background: var(--st-ok);
}
.warn .dot {
  background: var(--st-waiting);
  animation: soft-pulse 1.2s ease-in-out infinite;
}
.bad .dot {
  background: var(--st-fail);
}
.mock .dot {
  background: var(--accent);
}
.badge-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast);
}
.badge-leave-active {
  transition:
    transform var(--t-fast) var(--ease-in),
    opacity var(--t-fast);
}
.badge-enter-from,
.badge-leave-to {
  transform: scale(0.5);
  opacity: 0;
}
</style>
