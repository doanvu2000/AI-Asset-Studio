<script setup lang="ts">
import { ref, watch } from 'vue'
import { ImageOff } from '@lucide/vue'
import type { RecipeDemo } from '@/composables/recipePeek'
import BlobImage from './BlobImage.vue'

const props = defineProps<{ demo: RecipeDemo; alt: string; size?: number; full?: boolean }>()
const emit = defineEmits<{ failed: [] }>()

const loaded = ref(false)
const broken = ref(false)
watch(
  () => props.demo,
  () => {
    loaded.value = false
    broken.value = false
  }
)
</script>

<template>
  <BlobImage v-if="demo.type === 'blob'" :hash="demo.hash" :size="size" :full="full" :alt="alt" />
  <div v-else class="file checker" :class="{ loaded }">
    <img v-if="!broken" :src="demo.src" :alt="alt" decoding="async" draggable="false" @load="loaded = true" @error="((broken = true), emit('failed'))" />
    <span v-else class="ph"><ImageOff :size="18" /></span>
  </div>
</template>

<style scoped>
.file {
  position: relative;
  display: grid;
  place-items: center;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  opacity: 0;
  transition: opacity var(--t-base) var(--ease-out);
}
.loaded img {
  opacity: 1;
}
.ph {
  color: var(--text-3);
}
</style>
