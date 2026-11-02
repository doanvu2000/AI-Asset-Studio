<script setup lang="ts">
import { computed } from 'vue'
import { Box, Cloud, Cpu, Hand, Zap } from '@lucide/vue'
import type { Lane } from '@/api/types'
import { LANE_LABEL } from '@/composables/format'
import { useUiMode } from '@/composables/uiMode'

const props = defineProps<{ lane: Lane }>()
const { advanced } = useUiMode()
const icon = computed(() => ({ gpu: Zap, blender: Box, cloud: Cloud, cpu: Cpu, none: Hand })[props.lane])
</script>

<template>
  <span v-if="advanced" class="ui-pill lane" :class="`l-${lane}`" :title="`Làn ${LANE_LABEL[lane]}`">
    <component :is="icon" :size="12" :stroke-width="2.2" />
    {{ LANE_LABEL[lane] }}
  </span>
</template>

<style scoped>
.lane {
  gap: 4px;
  height: 20px;
  padding: 0 8px;
}
.l-gpu {
  color: var(--c4);
  background: var(--c4-bg);
}
.l-blender {
  color: var(--c1);
  background: var(--c1-bg);
}
.l-cloud {
  color: var(--c6);
  background: var(--c6-bg);
}
.l-cpu {
  color: var(--c2);
  background: var(--c2-bg);
}
</style>
