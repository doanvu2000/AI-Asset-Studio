<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { X } from '@lucide/vue'
import { onKeyStroke } from '@vueuse/core'
import type { BlobRef } from '@/api/types'
import { bytes } from '@/composables/format'
import BlobImage from './BlobImage.vue'

const props = defineProps<{ blob: BlobRef | null }>()
const emit = defineEmits<{ close: [] }>()
const closeBtn = ref<HTMLButtonElement | null>(null)

onKeyStroke('Escape', () => props.blob && emit('close'))
watch(
  () => props.blob,
  (b) => b && void nextTick(() => closeBtn.value?.focus())
)
</script>

<template>
  <Teleport to="body">
    <Transition name="lb">
      <div v-if="blob" class="scrim" role="dialog" aria-modal="true" :aria-label="blob.name ?? 'Ảnh'" @click.self="emit('close')">
        <figure class="sheet">
          <BlobImage :hash="blob.hash" full class="img" />
          <figcaption class="cap">
            <span class="name">{{ blob.name ?? blob.hash.slice(0, 12) }}</span>
            <span class="caption num">
              <template v-if="blob.width">{{ blob.width }}×{{ blob.height }} · </template>{{ bytes(blob.size) }}
            </span>
            <button ref="closeBtn" class="btn btn-plain btn-icon" aria-label="Đóng" @click="emit('close')"><X /></button>
          </figcaption>
        </figure>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 48px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.sheet {
  margin: 0;
  display: grid;
  gap: 0;
  width: min(78vh, 100%);
  border-radius: var(--r-xl);
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.img {
  width: 100%;
  border-radius: 0;
}
.cap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px 8px 16px;
}
.name {
  flex: 1;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lb-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.lb-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.lb-leave-active {
  transition: opacity var(--t-fast) var(--ease-in);
}
.lb-enter-from,
.lb-leave-to {
  opacity: 0;
}
.lb-enter-from .sheet {
  transform: scale(0.94);
}
</style>
