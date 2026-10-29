<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { onClickOutside } from '@vueuse/core'
import { Bot, ChevronRight } from '@lucide/vue'
import { useAgents } from '@/stores/agents'
import { activityText, clientName, toolLabel } from '@/composables/agentLabels'
import type { AgentActivity } from '@/api/types'
import RelTime from './RelTime.vue'

defineProps<{ compact?: boolean }>()

const agents = useAgents()
const router = useRouter()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
onClickOutside(root, () => (open.value = false))

const lead = computed(() => agents.live[0] ?? null)
const headline = computed(() => {
  const s = lead.value
  if (!s) return ''
  const who = clientName(s.client)
  return s.current ? `${who} đang ${toolLabel(s.current.tool)}` : `${who} đã kết nối`
})
const sub = computed(() => lead.value?.current?.detail || lead.value?.current?.summary || 'Đang chờ lệnh tiếp theo')
const timeline = computed(() => [...agents.events].reverse().slice(0, 14))

function go(e: AgentActivity): void {
  if (!e.run_id) return
  open.value = false
  void router.push(`/runs/${e.run_id}`)
}

function goCurrent(): void {
  const id = lead.value?.current?.run_id ?? lead.value?.last_run_id
  if (id) void router.push(`/runs/${id}`)
}
</script>

<template>
  <Transition name="agent-in">
    <div v-if="lead" ref="root" class="agent-wrap">
      <button
        type="button"
        class="agent"
        :class="{ busy: !!lead.current, compact }"
        :aria-expanded="open"
        :title="compact ? `${headline} — ${sub}` : 'Hoạt động của agent'"
        @click="open = !open"
      >
        <span class="ic"><Bot :size="15" :stroke-width="1.9" /><span class="pulse" aria-hidden="true" /></span>
        <span class="txt">
          <Transition name="swap" mode="out-in">
            <span :key="headline" class="h">{{ headline }}</span>
          </Transition>
          <Transition name="swap" mode="out-in">
            <span :key="sub" class="s">{{ sub }}</span>
          </Transition>
        </span>
        <ChevronRight class="chev" :size="14" />
      </button>
      <Transition name="pop">
        <div v-if="open" class="panel" role="dialog" aria-label="Hoạt động của agent">
          <header>
            <strong>{{ clientName(lead.client) }}</strong>
            <span class="caption secondary">{{ lead.calls }} lệnh · kết nối <RelTime :ts="lead.connected_at" /></span>
          </header>
          <button v-if="lead.current?.run_id || lead.last_run_id" type="button" class="btn open-run" @click="goCurrent">
            Mở lượt chạy agent đang làm
          </button>
          <TransitionGroup tag="ol" name="row" class="timeline">
            <li
              v-for="e in timeline"
              :key="`${e.session}-${e.ts}-${e.phase}`"
              :class="[`ph-${e.phase}`, { link: !!e.run_id }]"
              @click="go(e)"
            >
              <span class="dot" />
              <span class="what">{{ activityText(e) }}</span>
              <span v-if="e.phase === 'start' && e.summary" class="args mono">{{ e.summary }}</span>
              <RelTime class="when caption secondary" :ts="e.ts" />
            </li>
          </TransitionGroup>
        </div>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
.agent-wrap {
  position: relative;
  -webkit-app-region: no-drag;
  margin: 0 0 10px;
}
.agent {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.agent:hover {
  background: var(--bg-hover);
}
.ic {
  position: relative;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 7px;
  color: var(--accent);
  background: var(--bg-content);
}
.pulse {
  position: absolute;
  inset: -2px;
  border-radius: 9px;
  border: 1.5px solid var(--accent);
  opacity: 0;
}
.busy .pulse {
  animation: ring 1.6s var(--ease-out) infinite;
}
@keyframes ring {
  0% {
    transform: scale(0.9);
    opacity: 0.7;
  }
  100% {
    transform: scale(1.25);
    opacity: 0;
  }
}
.txt {
  display: grid;
  min-width: 0;
  flex: 1;
}
.h,
.s {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.h {
  font-size: var(--fs-caption);
  font-weight: 600;
}
.s {
  font-size: 11px;
  color: var(--text-2);
}
.chev {
  flex: none;
  color: var(--text-3);
}
.compact .txt,
.compact .chev {
  display: none;
}
.compact {
  justify-content: center;
  padding: 6px 0;
}
.panel {
  position: absolute;
  left: 0;
  bottom: calc(100% + 6px);
  z-index: 40;
  width: 340px;
  max-height: 420px;
  overflow: auto;
  padding: 12px;
  border-radius: var(--r-md);
  background: var(--bg-content);
  box-shadow: var(--shadow-3);
  display: grid;
  gap: 8px;
}
.panel header {
  display: grid;
  gap: 2px;
}
.open-run {
  justify-self: start;
}
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 2px;
}
.timeline li {
  display: grid;
  grid-template-columns: 10px 1fr auto;
  column-gap: 8px;
  align-items: baseline;
  padding: 4px 6px;
  border-radius: 6px;
  font-size: var(--fs-caption);
}
.timeline li.link {
  cursor: pointer;
}
.timeline li.link:hover {
  background: var(--bg-hover);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--st-pending);
  align-self: center;
}
.ph-start .dot,
.ph-wait .dot {
  background: var(--st-running);
}
.ph-end .dot,
.ph-connect .dot {
  background: var(--st-ok);
}
.ph-error .dot {
  background: var(--st-fail);
}
.what {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.when {
  grid-column: 3;
  grid-row: 1;
}
.args {
  grid-column: 2 / 4;
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.agent-in-enter-active,
.pop-enter-active {
  transition:
    opacity var(--t-base),
    transform var(--t-base) var(--ease-spring);
}
.agent-in-leave-active,
.pop-leave-active {
  transition:
    opacity var(--t-fast),
    transform var(--t-fast);
}
.agent-in-enter-from,
.agent-in-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(4px) scale(0.98);
}
.swap-enter-active,
.swap-leave-active {
  transition: opacity var(--t-fast);
}
.swap-enter-from,
.swap-leave-to {
  opacity: 0;
}
.row-enter-active {
  transition:
    opacity var(--t-base),
    transform var(--t-base);
}
.row-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}
@media (prefers-reduced-motion: reduce) {
  .busy .pulse {
    animation: none;
  }
}
</style>
