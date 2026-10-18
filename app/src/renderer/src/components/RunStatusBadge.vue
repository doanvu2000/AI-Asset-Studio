<script setup lang="ts">
import type { RunStatus } from '@/api/types'
import { RUN_LABEL } from '@/composables/format'

defineProps<{ status: RunStatus }>()
</script>

<template>
  <Transition name="badge" mode="out-in">
    <span :key="status" class="badge" :class="`s-${status}`">
      <span class="dot" />
      {{ RUN_LABEL[status] }}
    </span>
  </Transition>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 9px 0 8px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  font-weight: 600;
  white-space: nowrap;
  color: var(--text-2);
  background: var(--bg-active);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.s-running {
  color: var(--st-running);
  background: var(--st-running-soft);
}
.s-running .dot {
  animation: soft-pulse 1.6s ease-in-out infinite;
}
.s-awaiting_human {
  color: var(--st-human);
  background: var(--st-human-soft);
}
.s-succeeded {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.s-failed,
.s-interrupted {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.badge-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast);
}
.badge-leave-active {
  transition: opacity 100ms var(--ease-in);
}
.badge-enter-from {
  transform: scale(0.85);
  opacity: 0;
}
.badge-leave-to {
  opacity: 0;
}
</style>
