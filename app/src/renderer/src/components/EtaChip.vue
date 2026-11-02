<script setup lang="ts">
import { computed } from 'vue'
import type { RunRow, StepRow } from '@/api/types'
import { useNow } from '@/composables/clock'
import { activeSeconds, estimateEta, etaLabel, useRecipeMedians } from '@/composables/eta'

const props = defineProps<{ run: RunRow; steps?: StepRow[] }>()
const now = useNow()
const medians = useRecipeMedians()
const text = computed(() => {
  if (props.run.status !== 'running') return null
  const eta = estimateEta(medians.value.get(props.run.recipe_id), activeSeconds(props.run, props.steps, now.value))
  return eta ? etaLabel(eta) : null
})
</script>

<template>
  <span v-if="text" class="eta caption num" title="Ước lượng theo thời gian trung vị của các lượt đã xong">{{ text }}</span>
</template>

<style scoped>
.eta {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  color: var(--text-2);
  background: var(--bg-active);
  white-space: nowrap;
}
</style>
