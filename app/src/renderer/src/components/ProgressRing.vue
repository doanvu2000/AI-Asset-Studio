<script setup lang="ts">
import { computed } from 'vue'
import { useTweened } from '@/composables/motion'

const props = withDefaults(defineProps<{ value: number | null; size?: number; stroke?: number }>(), {
  size: 22,
  stroke: 2.5
})

const r = computed(() => (props.size - props.stroke) / 2)
const c = computed(() => 2 * Math.PI * r.value)
const tweened = useTweened(() => Math.min(1, Math.max(0, props.value ?? 0)), 320)
const offset = computed(() => c.value * (1 - tweened.value))
</script>

<template>
  <svg
    class="ring"
    :class="{ indeterminate: value === null }"
    :width="size"
    :height="size"
    :viewBox="`0 0 ${size} ${size}`"
    role="progressbar"
    :aria-valuenow="value === null ? undefined : Math.round(value * 100)"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <circle class="track" :cx="size / 2" :cy="size / 2" :r="r" :stroke-width="stroke" />
    <circle
      class="bar"
      :cx="size / 2"
      :cy="size / 2"
      :r="r"
      :stroke-width="stroke"
      :stroke-dasharray="c"
      :stroke-dashoffset="value === null ? c * 0.72 : offset"
    />
  </svg>
</template>

<style scoped>
.ring {
  transform: rotate(-90deg);
  flex: none;
}
.track {
  fill: none;
  stroke: var(--st-running-soft);
}
.bar {
  fill: none;
  stroke: var(--st-running);
  stroke-linecap: round;
}
.indeterminate {
  animation: spin-from 0.9s linear infinite;
}
@keyframes spin-from {
  from {
    transform: rotate(-90deg);
  }
  to {
    transform: rotate(270deg);
  }
}
</style>
