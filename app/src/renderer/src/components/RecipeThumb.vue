<script setup lang="ts">
import { computed } from 'vue'
import { Boxes } from '@lucide/vue'
import { recipeArt } from '@/assets/art'

const props = withDefaults(defineProps<{ id: string; size?: number }>(), { size: 44 })
const src = computed(() => recipeArt(props.id))
</script>

<template>
  <span class="thumb" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true">
    <img v-if="src" :src="src" alt="" :width="size" :height="size" decoding="async" draggable="false" />
    <Boxes v-else :size="Math.round(size * 0.46)" :stroke-width="1.6" />
  </span>
</template>

<style scoped>
.thumb {
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 28%;
  color: var(--accent);
  background: var(--accent-soft);
}
.thumb:has(img) {
  background: radial-gradient(circle at 50% 45%, var(--accent-soft), transparent 70%);
}
img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
}
</style>
