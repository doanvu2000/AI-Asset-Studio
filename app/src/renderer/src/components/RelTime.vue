<script setup lang="ts">
import { computed } from 'vue'
import { useRelativeNow } from '@/composables/clock'
import { absoluteTime, relativeTime } from '@/composables/format'

const props = defineProps<{ ts: number }>()
const now = useRelativeNow()
const text = computed(() => relativeTime(props.ts, now.value))
</script>

<template>
  <time :datetime="new Date(ts * 1000).toISOString()" :title="absoluteTime(ts)">{{ text }}</time>
</template>
