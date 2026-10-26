<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ImageOff } from '@lucide/vue'
import { blobUrl, thumbUrl, useConnection } from '@/stores/connection'

const props = withDefaults(defineProps<{ hash: string; size?: number; alt?: string; full?: boolean; contain?: boolean }>(), {
  size: 256,
  alt: '',
  full: false,
  contain: true
})

// Thumbnails can 503/422 while the core is still generating them; degrade to the original, then a placeholder.
const stage = ref<0 | 1 | 2>(props.full ? 1 : 0)
const loaded = ref(false)
const conn = useConnection()
// A restarted core listens on a new port; images that failed against the old one must retry from scratch.
watch(
  [() => props.hash, () => conn.transport],
  () => {
    stage.value = props.full ? 1 : 0
    loaded.value = false
  }
)
const src = computed(() => (stage.value === 0 ? thumbUrl(props.hash, props.size) : stage.value === 1 ? blobUrl(props.hash) : ''))

function onError(): void {
  if (stage.value < 2) stage.value = (stage.value + 1) as 1 | 2
}
</script>

<template>
  <div class="blob checker" :class="{ loaded }">
    <img
      v-if="stage < 2 && src"
      :key="src"
      :src="src"
      :alt="alt"
      loading="lazy"
      decoding="async"
      draggable="false"
      :style="{ objectFit: contain ? 'contain' : 'cover' }"
      @load="loaded = true"
      @error="onError"
    />
    <span v-else class="ph"><ImageOff :size="18" /></span>
  </div>
</template>

<style scoped>
.blob {
  position: relative;
  overflow: hidden;
  border-radius: inherit;
  aspect-ratio: 1;
}
img {
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity var(--t-base) var(--ease-out);
}
.loaded img {
  opacity: 1;
}
.ph {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--text-3);
}
</style>
