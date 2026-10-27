<script setup lang="ts">
import { computed } from 'vue'
import type { QaResult } from '@/api/types'
import { QA_TIER_TEXT, qaScore, qaTier } from '@/composables/qa'

const props = defineProps<{ qa: QaResult | null | undefined; size?: 'sm' | 'md' }>()
const score = computed(() => qaScore(props.qa))
const tier = computed(() => (score.value === null ? null : qaTier(score.value)))
const title = computed(() => {
  if (score.value === null || !tier.value) return ''
  const lines = [`Điểm chất lượng ${score.value}/100 · ${QA_TIER_TEXT[tier.value]}`]
  for (const p of props.qa?.parts ?? []) lines.push(`${p.label}: ${p.score}`)
  for (const i of props.qa?.issues ?? []) lines.push(`• ${i}`)
  return lines.join('\n')
})
</script>

<template>
  <span v-if="score !== null" class="qa num" :class="[`t-${tier}`, size ?? 'sm']" role="img" :title="title" :aria-label="`Điểm chất lượng ${score}/100`">{{ score }}</span>
</template>

<style scoped>
.qa {
  flex: none;
  display: inline-grid;
  place-items: center;
  min-width: 26px;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 700;
  line-height: 16px;
  transition:
    color var(--t-fast),
    background-color var(--t-fast);
}
.qa.md {
  min-width: 34px;
  padding: 3px 9px;
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
