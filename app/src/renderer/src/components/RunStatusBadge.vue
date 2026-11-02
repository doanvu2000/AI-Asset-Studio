<script setup lang="ts">
import { computed } from 'vue'
import type { RunStatus } from '@/api/types'
import { RUN_LABEL } from '@/composables/format'

const props = defineProps<{ status: RunStatus }>()

const TONE: Partial<Record<RunStatus, string>> = {
  running: 't-run',
  awaiting_human: 't-human',
  succeeded: 't-ok',
  failed: 't-fail',
  interrupted: 't-fail'
}
const tone = computed(() => TONE[props.status] ?? '')
</script>

<template>
  <Transition name="badge" mode="out-in">
    <span :key="status" class="ui-pill" :class="[tone, { pulse: status === 'running' }]">
      <span class="dot" />
      {{ RUN_LABEL[status] }}
    </span>
  </Transition>
</template>

<style scoped>
.pulse > .dot {
  animation: soft-pulse 1.6s ease-in-out infinite;
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
