<script setup lang="ts">
import { computed } from 'vue'
import { Hand, Hourglass, Minus, Recycle, X } from '@lucide/vue'
import type { StepStatus } from '@/api/types'
import ProgressRing from './ProgressRing.vue'

const props = withDefaults(defineProps<{ status: StepStatus; progress?: number | null; size?: number }>(), {
  progress: null,
  size: 24
})

// Running keeps one key so progress updates never remount the ring.
const key = computed(() => props.status)
</script>

<template>
  <span class="glyph" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true">
    <Transition name="morph">
      <span v-if="status === 'running'" :key="key" class="g">
        <ProgressRing :value="progress" :size="size" />
      </span>
      <span v-else-if="status === 'succeeded'" :key="key" class="g disc ok">
        <svg viewBox="0 0 24 24" class="check"><path d="M7 12.5l3.2 3.2L17 9" /></svg>
      </span>
      <span v-else-if="status === 'failed'" :key="key" class="g disc fail"><X :stroke-width="3" /></span>
      <span v-else-if="status === 'cached'" :key="key" class="g disc cached"><Recycle :stroke-width="2.4" /></span>
      <span v-else-if="status === 'waiting_resource'" :key="key" class="g waiting"><Hourglass :stroke-width="2.2" /></span>
      <span v-else-if="status === 'awaiting_human'" :key="key" class="g disc human"><Hand :stroke-width="2.4" /></span>
      <span v-else-if="status === 'cancelled' || status === 'skipped'" :key="key" class="g disc muted"><Minus :stroke-width="3" /></span>
      <span v-else-if="status === 'ready'" :key="key" class="g"><span class="hollow ready" /></span>
      <span v-else :key="key" class="g"><span class="hollow" /></span>
    </Transition>
  </span>
</template>

<style scoped>
.glyph {
  position: relative;
  display: inline-grid;
  flex: none;
}
.g {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}
.disc {
  border-radius: 50%;
}
.disc :deep(svg) {
  width: 58%;
  height: 58%;
}
.ok {
  background: var(--st-ok);
  color: #fff;
}
.fail {
  background: var(--st-fail);
  color: #fff;
}
.cached {
  background: var(--st-cached-soft);
  color: var(--st-cached);
}
.human {
  background: var(--st-human);
  color: #fff;
}
.human :deep(svg) {
  animation: soft-pulse 2.4s var(--ease-out) infinite;
}
.muted {
  background: var(--bg-active);
  color: var(--text-3);
}
.waiting {
  color: var(--st-waiting);
}
.waiting :deep(svg) {
  width: 72%;
  height: 72%;
  animation: soft-pulse 1.8s ease-in-out infinite;
}
.hollow {
  width: 42%;
  height: 42%;
  border-radius: 50%;
  border: 2px solid var(--st-pending);
  opacity: 0.6;
}
.hollow.ready {
  width: 62%;
  height: 62%;
  border-color: var(--st-running);
  opacity: 1;
}
.check {
  fill: none;
  stroke: currentColor;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 16;
  stroke-dashoffset: 16;
  animation: draw 320ms 80ms var(--ease-out) forwards;
}
@keyframes draw {
  to {
    stroke-dashoffset: 0;
  }
}

.morph-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast) var(--ease-out);
}
.morph-leave-active {
  transition:
    transform var(--t-fast) var(--ease-in),
    opacity 120ms var(--ease-in);
}
.morph-enter-from {
  transform: scale(0.4);
  opacity: 0;
}
.morph-leave-to {
  transform: scale(1.3);
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .check {
    stroke-dashoffset: 0;
    animation: none;
  }
}
</style>
