<script setup lang="ts">
import { computed } from 'vue'
import { reviewTier } from '@/composables/review'

const props = defineProps<{ score: number; history?: number[]; size?: 'sm' | 'md' }>()
const tier = computed(() => reviewTier(props.score))
const text = computed(() => `${Number(props.score.toFixed(1))}/10`)
const title = computed(() =>
  props.history && props.history.length > 1 ? `Điểm chấm ${text.value} · các vòng: ${props.history.join(' → ')}` : `Điểm chấm ${text.value}`
)
</script>

<template>
  <span class="sb num" :class="[`t-${tier}`, size ?? 'sm']" role="img" :title="title" :aria-label="title">{{ text }}</span>
</template>

<style scoped>
.sb {
  flex: none;
  display: inline-grid;
  place-items: center;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 700;
  line-height: 16px;
  white-space: nowrap;
}
.sb.md {
  padding: 3px 10px;
  font-size: var(--fs-callout);
}
.t-good {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.t-warn {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.t-bad {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
</style>
