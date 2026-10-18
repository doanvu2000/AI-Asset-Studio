<script setup lang="ts">
import { computed } from 'vue'
import { useTweened } from '@/composables/motion'

const props = withDefaults(defineProps<{ value: number; decimals?: number; duration?: number; format?: (n: number) => string }>(), {
  decimals: 0,
  duration: 300,
  format: undefined
})

const tweened = useTweened(() => props.value, props.duration)
const nf = computed(() => new Intl.NumberFormat('vi-VN', { maximumFractionDigits: props.decimals, minimumFractionDigits: props.decimals }))
const text = computed(() => (props.format ? props.format(tweened.value) : nf.value.format(tweened.value)))
</script>

<template>
  <span class="num">{{ text }}</span>
</template>
