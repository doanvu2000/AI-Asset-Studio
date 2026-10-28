<script setup lang="ts">
import { computed, ref } from 'vue'
import { Repeat } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import { bytes } from '@/composables/format'

const props = withDefaults(defineProps<{ blob: BlobRef; poster?: BlobRef; compact?: boolean }>(), { compact: false })

// Teasers are judged on their loop seam, so looped clips replay by default.
const loop = ref(/loop/i.test(props.blob.name ?? ''))
const src = computed(() => blobUrl(props.blob.hash))
const posterUrl = computed(() => (props.poster ? blobUrl(props.poster.hash) : undefined))
</script>

<template>
  <div class="vw vw-backdrop video">
    <video :src="src" :poster="posterUrl" :loop="loop" controls autoplay muted playsinline preload="metadata" />
    <div v-if="!compact" class="bar">
      <label class="btn btn-plain small" :class="{ on: loop }">
        <input v-model="loop" type="checkbox" class="sr-only" />
        <Repeat :size="14" />
        Lặp
      </label>
      <span class="msg num">{{ blob.name }} · {{ blob.mime }} · {{ bytes(blob.size) }}</span>
    </div>
  </div>
</template>

<style scoped>
.video {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
}
.video video {
  flex: 1;
  min-height: 0;
  width: 100%;
  object-fit: contain;
  border-radius: 6px;
  background: #000;
}
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.bar .msg {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.on {
  color: var(--accent);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
