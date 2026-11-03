<script setup lang="ts">
import { ART } from '@/assets/art'
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { FolderKanban, Images, Inbox, LayoutDashboard, ListChecks, Plus, ScanSearch, Settings, SlidersHorizontal, Workflow } from '@lucide/vue'
import { WORKFLOW_ACTIVE } from '@/api/types'
import { useRuns } from '@/stores/runs'
import { useFlows } from '@/stores/flows'
import { useGates } from '@/stores/gates'
import { useConnection } from '@/stores/connection'
import { useUiMode } from '@/composables/uiMode'
import { useReducedMotion } from '@/composables/motion'
import TweenNumber from './TweenNumber.vue'
import AgentActivity from './AgentActivity.vue'

defineProps<{ compact?: boolean }>()

const route = useRoute()
const runs = useRuns()
const flows = useFlows()
const gates = useGates()
const conn = useConnection()
const { advanced, toggle: toggleMode } = useUiMode()
const reducedMotion = useReducedMotion()
const sidebar = ref<HTMLElement>()

// The pill is re-created under the newly active item; FLIP it from the old item's rect so it slides like a shared element.
watch(
  () => route.name,
  async () => {
    const from = sidebar.value?.querySelector('.pill')?.getBoundingClientRect()
    await nextTick()
    const el = sidebar.value?.querySelector<HTMLElement>('.pill')
    if (!from || !el || reducedMotion.value) return
    const to = el.getBoundingClientRect()
    if (!to.width || !to.height) return
    el.animate(
      [{ transformOrigin: '0 0', transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})` }, { transformOrigin: '0 0', transform: 'none' }],
      { duration: 240, easing: 'cubic-bezier(0.2, 0.9, 0.25, 1)' }
    )
  },
  { flush: 'pre' }
)

// Only "Cần duyệt" carries a number: it is the one place that needs the user. Lượt chạy gets a quiet
// activity dot while something is really executing (not parked at a gate).
const groups = computed(() => allGroups.value.filter((g) => advanced.value || g.title !== 'Nâng cao'))
const allGroups = computed(() => [
  {
    title: 'Làm việc',
    items: [
      { to: '/', name: 'dashboard', label: 'Tổng quan', icon: LayoutDashboard, badge: 0, busy: false, key: '1' },
      { to: '/new', name: 'new', label: 'Tạo mới', icon: Plus, badge: 0, busy: false, key: '2' },
      { to: '/gates', name: 'gates', label: 'Cần duyệt', icon: Inbox, badge: gates.count, busy: false, key: '3' }
    ]
  },
  {
    title: 'Kho',
    items: [
      { to: '/gallery', name: 'gallery', label: 'Thư viện', icon: Images, badge: 0, busy: false, key: '4' },
      { to: '/projects', name: 'projects', label: 'Dự án', icon: FolderKanban, badge: 0, busy: false, key: '5' },
      { to: '/runs', name: 'history', label: 'Lịch sử', icon: ListChecks, badge: 0, busy: runs.runningRuns.length > 0, key: '6' }
    ]
  },
  {
    title: 'Nâng cao',
    items: [
      { to: '/recipes', name: 'recipes', label: 'Công thức', icon: Workflow, badge: 0, busy: flows.runList.some((r) => WORKFLOW_ACTIVE.has(r.status)), key: '7' },
      { to: '/audit', name: 'audit', label: 'Kiểm tra lỗi', icon: ScanSearch, badge: 0, busy: false, key: '8' }
    ]
  }
])

function tip(it: { label: string; badge: number; busy: boolean; key: string }): string {
  const extra = it.badge ? ` · ${it.badge} đang chờ` : it.busy && it.key ? ` · ${runs.runningRuns.length} đang chạy` : it.busy ? ' · đang chạy' : ''
  return it.key ? `${it.label}${extra}  (Ctrl+${it.key})` : `${it.label}${extra}`
}

const ROUTE_GROUP: Record<string, string[]> = {
  history: ['runs', 'run', 'batch', 'batch-detail'],
  recipes: ['recipes', 'recipe']
}

function isActive(name: string): boolean {
  const r = String(route.name ?? '')
  if (name === 'recipes') return r.startsWith('flow') || ROUTE_GROUP.recipes!.includes(r)
  return r === name || (ROUTE_GROUP[name]?.includes(r) ?? false) || (name === 'projects' && r === 'project')
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
  <aside ref="sidebar" class="sidebar" :class="{ compact }">
    <div class="brand">
      <img class="logo" :src="ART.appIcon" alt="" width="22" height="22" draggable="false" />
      <span class="brand-name">Asset Studio</span>
    </div>

    <nav aria-label="Điều hướng chính">
      <template v-for="g in groups" :key="g.title">
      <h2 class="group">{{ g.title }}</h2>
      <RouterLink
        v-for="it in g.items"
        :key="it.to"
        :to="it.to"
        class="item"
        :class="{ active: isActive(it.name) }"
        :aria-current="isActive(it.name) ? 'page' : undefined"
        :title="tip(it)"
      >
        <span v-if="isActive(it.name)" class="pill" />
        <component :is="it.icon" class="ic" :size="16" :stroke-width="1.75" />
        <span class="label" :title="it.label">{{ it.label }}</span>
        <Transition name="badge">
          <span v-if="it.badge > 0" class="count human" :aria-label="`${it.badge} đang chờ`">
            <TweenNumber :value="it.badge" :duration="200" />
          </span>
          <span v-else-if="it.busy" class="busy" role="img" aria-label="Đang chạy" />
        </Transition>
      </RouterLink>
      </template>
    </nav>

    <AgentActivity class="agent-slot" :compact="compact" />
    <nav class="nav-foot" aria-label="Cài đặt">
      <RouterLink
        to="/settings"
        class="item"
        :class="{ active: isActive('settings') }"
        :aria-current="isActive('settings') ? 'page' : undefined"
        title="Cài đặt"
      >
        <span v-if="isActive('settings')" class="pill" />
        <Settings class="ic" :size="16" :stroke-width="1.75" />
        <span class="label">Cài đặt</span>
      </RouterLink>
    </nav>

    <div class="foot">
      <button
        type="button"
        class="mode"
        :aria-pressed="advanced"
        :title="advanced ? 'Đang ở chế độ Nâng cao, bấm để về Đơn giản' : 'Đang ở chế độ Đơn giản, bấm để hiện thêm công cụ'"
        @click="toggleMode"
      >
        <SlidersHorizontal :size="12" :stroke-width="2" />
        <span class="mode-text">{{ advanced ? 'Nâng cao' : 'Đơn giản' }}</span>
      </button>
      <span class="conn" :class="status.tone" :title="compact ? status.text : undefined" role="status">
        <span class="dot" />
        <span class="conn-text" :title="status.text">{{ status.text }}</span>
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
  border-right: 1px solid var(--hair-soft);
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
  font-size: var(--fs-body);
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
}
nav {
  -webkit-app-region: no-drag;
  display: grid;
  gap: 2px;
}
.group {
  margin: 18px 0 4px;
  padding: 0 10px;
  font-size: var(--fs-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-2);
  white-space: nowrap;
}
.group:first-child {
  margin-top: 2px;
}
.item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 34px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  color: var(--text);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition:
    background-color var(--t-fast) var(--ease-out),
    color var(--t-fast) var(--ease-out);
}
.item.active {
  font-weight: 600;
}
.item:hover:not(.active) {
  background: var(--bg-hover);
}
.pill {
  position: absolute;
  inset: 0;
  border-radius: var(--r-sm);
  background: var(--surface);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
  z-index: 0;
}
.ic,
.label,
.count {
  position: relative;
  z-index: 1;
}
.ic {
  color: var(--ink-2);
  flex: none;
  transition: color var(--t-fast) var(--ease-out);
}
.active .ic {
  color: var(--accent);
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
  font-size: var(--fs-caption);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
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
.agent-slot {
  margin-top: auto;
}
.agent-slot + .nav-foot {
  margin-top: 0;
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
.mode {
  -webkit-app-region: no-drag;
  display: flex;
  width: fit-content;
  align-items: center;
  gap: 6px;
  height: 26px;
  margin-bottom: 8px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-sm);
  font-size: var(--fs-caption);
  font-weight: 500;
  color: var(--text-2);
  background: var(--fill);
  cursor: pointer;
  transition: background-color var(--t-fast) var(--ease-out);
}
.mode:hover {
  background: var(--fill-2);
}
.compact .mode-text {
  display: none;
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
  overflow-wrap: anywhere;
}
.compact .group {
  height: 1px;
  margin: 8px 6px;
  padding: 0;
  overflow: hidden;
  color: transparent;
  background: var(--separator);
}
.compact .group:first-child {
  display: none;
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
