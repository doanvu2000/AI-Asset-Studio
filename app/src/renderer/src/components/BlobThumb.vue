<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Box, File, FileBraces, FileCode, FileText, Layers } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { fileBadge, isImage, type FileBadge } from './viewers/detect'
import BlobImage from './BlobImage.vue'

const props = withDefaults(defineProps<{ blob: BlobRef; size?: number }>(), { size: 256 })

const GLYPH: Record<FileBadge, { icon: Component; label: string }> = {
  glb: { icon: Box, label: 'GLB' },
  spine: { icon: Layers, label: 'Spine' },
  atlas: { icon: Layers, label: 'Atlas' },
  json: { icon: FileBraces, label: 'JSON' },
  html: { icon: FileCode, label: 'HTML' },
  text: { icon: FileText, label: 'Văn bản' },
  file: { icon: File, label: 'Tệp' }
}
const glyph = computed(() => GLYPH[fileBadge(props.blob)])
</script>

<template>
  <BlobImage v-if="isImage(blob)" :hash="blob.hash" :size="size" />
  <div v-else class="glyph" :class="`g-${fileBadge(blob)}`">
    <component :is="glyph.icon" class="ic" :size="Math.max(16, Math.round(size / 9))" :stroke-width="1.5" />
    <span class="tag">{{ glyph.label }}</span>
    <span v-if="size >= 160 && blob.name" class="nm">{{ blob.name }}</span>
  </div>
</template>

<style scoped>
.glyph {
  aspect-ratio: 1;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 6px;
  padding: 10px;
  border-radius: inherit;
  color: var(--text-3);
  background:
    radial-gradient(90% 80% at 50% 35%, var(--bg-elevated), transparent 75%),
    var(--bg-sunken);
}
.g-glb {
  color: var(--lane-blender);
}
.g-atlas,
.g-spine {
  color: var(--lane-gpu);
}
.g-json {
  color: var(--lane-cloud);
}
.tag {
  font-size: var(--fs-caption);
  font-weight: 700;
  letter-spacing: 0.04em;
}
.nm {
  max-width: 100%;
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
