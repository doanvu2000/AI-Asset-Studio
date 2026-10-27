<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { FileQuestionMark } from '@lucide/vue'
import { blobUrl } from '@/stores/connection'
import { bytes } from '@/composables/format'
import type { AssetView } from './detect'
import SpriteSheetViewer from './SpriteSheetViewer.vue'
import TextViewer from './TextViewer.vue'
import TileViewer from './TileViewer.vue'
import ViewerLoading from './ViewerLoading.vue'
import './viewer.css'

// three.js and spine-player are large; they load only when such an asset is actually opened.
const GlbViewer = defineAsyncComponent({ loader: () => import('./GlbViewer.vue'), loadingComponent: ViewerLoading, delay: 120 })
const SpineViewer = defineAsyncComponent({ loader: () => import('./SpineViewer.vue'), loadingComponent: ViewerLoading, delay: 120 })

withDefaults(defineProps<{ view: AssetView; compact?: boolean }>(), { compact: false })
</script>

<template>
  <GlbViewer v-if="view.kind === 'glb'" :key="view.blob.hash" :blob="view.blob" :compact="compact" />
  <SpriteSheetViewer v-else-if="view.kind === 'sprite' && view.sheet && view.meta" :sheet="view.sheet" :meta="view.meta" :normal="view.normal" :compact="compact" />
  <SpineViewer v-else-if="view.kind === 'spine' && view.spine" :spine="view.spine" :compact="compact" />
  <TileViewer
    v-else-if="view.kind === 'tile'"
    :key="view.blob.hash"
    :blob="view.blob"
    :tiles="view.tiles"
    :report="view.report"
    :normal="view.normal"
    :pixel="view.pixel"
    :normals="view.normals"
    :pixels="view.pixels"
    :wang="view.wang"
    :compact="compact"
  />
  <TextViewer v-else-if="view.kind === 'text'" :blob="view.blob" />
  <div v-else-if="view.kind === 'image'" class="vw checker img">
    <img
      :src="blobUrl(view.blob.hash)"
      :alt="view.blob.name ?? ''"
      :class="{ pixel: Math.max(view.blob.width ?? 999, view.blob.height ?? 999) <= 256 }"
      decoding="async"
      draggable="false"
    />
  </div>
  <div v-else class="vw vw-backdrop">
    <div class="vw-state">
      <FileQuestionMark :size="24" />
      <strong>Chưa xem trước được loại tệp này</strong>
      <span class="msg num">{{ view.blob.mime }} · {{ bytes(view.blob.size) }}</span>
    </div>
  </div>
</template>

<style scoped>
.img {
  display: grid;
  place-items: center;
}
.img img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.img img.pixel {
  image-rendering: pixelated;
}
</style>
