<script setup lang="ts">
import { computed, ref } from 'vue'
import { ScanSearch } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import SimilarDrawer from './SimilarDrawer.vue'
import { useVersions } from '@/stores/versions'

const props = defineProps<{ hash: string; blob?: BlobRef; versionId?: string; compact?: boolean }>()
const versions = useVersions()
const open = ref(false)

const version = computed(() => (props.versionId ? versions.map.get(props.versionId) : undefined) ?? versions.byHash.get(props.hash))
const isImage = computed(() => {
  const b = props.blob ?? version.value?.blob
  return !b || b.mime.startsWith('image/')
})
</script>

<template>
  <template v-if="isImage">
    <button
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact }"
      :aria-label="compact ? 'Tìm giống' : undefined"
      aria-haspopup="dialog"
      title="Tìm asset giống trong thư viện, hoặc tạo thêm asset giống cái này"
      @click="open = true"
    >
      <ScanSearch />
      <span v-if="!compact">Tìm giống</span>
    </button>
    <SimilarDrawer
      :open="open"
      :hash="hash"
      :version-id="version?.id"
      :project-id="version?.project_id"
      :name="version?.asset_name.trim() || undefined"
      @close="open = false"
    />
  </template>
</template>
