<script setup lang="ts">
import { computed } from 'vue'
import { Box, Cloud, Cpu, Hand, Zap } from '@lucide/vue'
import type { Lane } from '@/api/types'
import { LANE_LABEL } from '@/composables/format'

const props = defineProps<{ lane: Lane }>()
const icon = computed(() => ({ gpu: Zap, blender: Box, cloud: Cloud, cpu: Cpu, none: Hand })[props.lane])
</script>

<template>
  <span class="lane" :title="`Làn ${LANE_LABEL[lane]}`">
    <component :is="icon" :size="11" :stroke-width="2.2" />
    {{ LANE_LABEL[lane] }}
  </span>
</template>

<style scoped>
.lane {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  font-size: var(--fs-caption);
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--text-3);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  white-space: nowrap;
}
</style>
