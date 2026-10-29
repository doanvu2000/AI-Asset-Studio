<script setup lang="ts">
import { computed } from 'vue'
import { Bot } from '@lucide/vue'
import { useAgents } from '@/stores/agents'
import { clientName, toolLabel } from '@/composables/agentLabels'

const props = defineProps<{ runId: string }>()
const agents = useAgents()

const session = computed(() => agents.live.find((s) => s.current?.run_id === props.runId || s.last_run_id === props.runId) ?? null)
const text = computed(() => {
  const s = session.value
  if (!s) return ''
  const who = clientName(s.client)
  if (s.current?.run_id === props.runId) return `${who} đang ${toolLabel(s.current.tool)}${s.current.detail ? ` — ${s.current.detail}` : ''}`
  return `${who} đã chạy lượt này qua MCP`
})
</script>

<template>
  <Transition name="banner">
    <div v-if="session" class="agent-banner" :class="{ live: session.current?.run_id === runId }" role="status">
      <Bot :size="14" :stroke-width="1.9" />
      <Transition name="swap" mode="out-in">
        <span :key="text" class="t">{{ text }}</span>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
.agent-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: var(--r-md);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--fs-caption);
}
.t {
  color: var(--text);
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.live svg {
  animation: soft-pulse 1.2s ease-in-out infinite;
}
.banner-enter-active,
.banner-leave-active {
  transition:
    opacity var(--t-base),
    transform var(--t-base) var(--ease-out);
}
.banner-enter-from,
.banner-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
.swap-enter-active,
.swap-leave-active {
  transition: opacity var(--t-fast);
}
.swap-enter-from,
.swap-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .live svg {
    animation: none;
  }
}
</style>
