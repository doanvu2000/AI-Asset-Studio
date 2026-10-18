<script setup lang="ts">
import { ART } from '@/assets/art'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { motion } from 'motion-v'
import { Images, Inbox, Layers, LayoutDashboard, ListChecks, Plus, Settings, Workflow } from '@lucide/vue'
import { useRuns } from '@/stores/runs'
import { useGates } from '@/stores/gates'
import { useConnection } from '@/stores/connection'
import TweenNumber from './TweenNumber.vue'

defineProps<{ compact?: boolean }>()

const route = useRoute()
const runs = useRuns()
const gates = useGates()
const conn = useConnection()

// Only "Cần duyệt" carries a number: it is the one place that needs the user. Lượt chạy gets a quiet
// activity dot while something is really executing (not parked at a gate).
const items = computed(() => [
  { to: '/', name: 'dashboard', label: 'Tổng quan', icon: LayoutDashboard, badge: 0, busy: false, key: '1' },
  { to: '/new', name: 'new', label: 'Tạo mới', icon: Plus, badge: 0, busy: false, key: '2' },
  { to: '/runs', name: 'runs', label: 'Lượt chạy', icon: ListChecks, badge: 0, busy: runs.runningRuns.length > 0, key: '3' },
  { to: '/gates', name: 'gates', label: 'Cần duyệt', icon: Inbox, badge: gates.count, busy: false, key: '4' },
  { to: '/gallery', name: 'gallery', label: 'Thư viện', icon: Images, badge: 0, busy: false, key: '5' },
  { to: '/recipes', name: 'recipes', label: 'Quy trình', icon: Workflow, badge: 0, busy: false, key: '6' },
  { to: '/batch', name: 'batch', label: 'Hàng loạt', icon: Layers, badge: 0, busy: false, key: '7' }
])

function tip(it: { label: string; badge: number; busy: boolean; key: string }): string {
  const extra = it.badge ? ` · ${it.badge} đang chờ` : it.busy ? ` · ${runs.runningRuns.length} đang chạy` : ''
  return `${it.label}${extra}  (Ctrl+${it.key})`
}

function isActive(name: string): boolean {
  const r = String(route.name ?? '')
  return r === name || (name === 'runs' && r === 'run') || (name === 'recipes' && r === 'recipe') || (name === 'batch' && r === 'batch-detail')
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
  <aside class="sidebar" :class="{ compact }">
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
        :title="tip(it)"
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
          <span v-if="it.badge > 0" class="count human" :aria-label="`${it.badge} đang chờ`">
            <TweenNumber :value="it.badge" :duration="200" />
          </span>
          <span v-else-if="it.busy" class="busy" role="img" aria-label="Đang chạy" />
        </Transition>
      </RouterLink>
    </nav>

    <nav class="nav-foot" aria-label="Cài đặt">
      <RouterLink
        to="/settings"
        class="item"
        :class="{ active: isActive('settings') }"
        :aria-current="isActive('settings') ? 'page' : undefined"
        title="Cài đặt"
      >
        <motion.span v-if="isActive('settings')" layout-id="nav-pill" class="pill" :transition="{ type: 'spring', stiffness: 520, damping: 40 }" />
        <Settings class="ic" :size="16" :stroke-width="2" />
        <span class="label">Cài đặt</span>
      </RouterLink>
    </nav>

    <div class="foot">
      <span class="conn" :class="status.tone" :title="compact ? status.text : undefined" role="status">
        <span class="dot" />
        <span class="conn-text">{{ status.text }}</span>
      </span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  -webkit-app-region: drag;
  display: flex;
  flex-direction: column;
  width: var(--pane-w, var(--sidebar-w));
  flex: none;
  overflow: hidden;
  padding: 0 10px 12px;
  background: var(--bg-sidebar);
  border-right: 0.5px solid var(--separator);
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  height: var(--toolbar-h);
  padding: 0 9px;
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
  white-space: nowrap;
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
  padding: 0 12px;
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
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
.busy {
  position: relative;
  z-index: 1;
  flex: none;
  width: 12px;
  height: 12px;
  margin-right: 4px;
  border-radius: 50%;
  border: 2px solid var(--st-running-soft);
  border-top-color: var(--st-running);
  animation: spin 0.9s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .busy {
    animation: none;
    border-color: var(--st-running);
  }
}
.count.human {
  color: var(--bg-content);
  background: var(--st-human);
}
.nav-foot {
  -webkit-app-region: no-drag;
  display: grid;
  margin-top: auto;
  margin-bottom: 10px;
}
.foot {
  padding: 0 6px;
}
.conn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 100%;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.conn-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.compact .brand-name,
.compact .label,
.compact .conn-text {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
.compact .count {
  position: absolute;
  top: 6px;
  right: 7px;
  min-width: 0;
  width: 8px;
  height: 8px;
  padding: 0;
  font-size: 0;
  background: var(--text-3);
  box-shadow: 0 0 0 2px var(--bg-sidebar);
}
.compact .busy {
  position: absolute;
  top: 5px;
  right: 6px;
  width: 10px;
  height: 10px;
  margin: 0;
}
.compact .count.human {
  background: var(--st-human);
}
.compact .foot {
  padding: 0 0 0 16px;
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
