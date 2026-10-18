<script setup lang="ts">
import { computed } from 'vue'
import { useNow } from '@/composables/clock'
import { clockDuration } from '@/composables/format'

const props = defineProps<{ from: number }>()
const now = useNow()
const text = computed(() => clockDuration(now.value - props.from))
</script>

<template>
  <span class="num" :aria-label="`Đã chạy ${text}`">{{ text }}</span>
</template>
