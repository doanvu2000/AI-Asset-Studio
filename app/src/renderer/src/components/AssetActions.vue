<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpRight, Check, Download, FolderOpen, GripVertical, Link2, LoaderCircle } from '@lucide/vue'
import { useAssetFile } from '@/composables/assetFile'
import BundleMenu from './BundleMenu.vue'
import { exportName, useVersions } from '@/stores/versions'

const props = defineProps<{
  hash: string
  filename?: string
  runId?: string
  versionId?: string
  /** Step whose outputs "Xuất thư mục" bundles; defaults to the version's step. */
  stepId?: string
  /** Icon-only buttons with tooltips, for tight toolbars and viewer overlays. */
  compact?: boolean
}>()

const route = useRoute()
const router = useRouter()
const versions = useVersions()
const file = useAssetFile()

const version = computed(() => (props.versionId ? versions.map.get(props.versionId) : undefined) ?? versions.byHash.get(props.hash))
const name = computed(() => props.filename ?? (version.value ? exportName(version.value) : undefined))
const runLink = computed(() => {
  const id = props.runId ?? version.value?.run_id
  return id && !(route.name === 'run' && route.params.id === id) ? id : null
})
const bundleStep = computed(() => props.stepId ?? version.value?.step_id)
const offTitle = 'Chỉ dùng được trong app desktop'
</script>

<template>
  <div class="asset-actions" :class="{ compact }" role="group" aria-label="Lấy tệp ra">
    <button
      type="button"
      class="btn"
      :class="{ 'btn-icon': compact }"
      :disabled="!file.available || !!file.busy.value"
      :title="file.available ? 'Lưu một bản sao vào thư mục bạn chọn' : offTitle"
      :aria-label="compact ? 'Xuất…' : undefined"
      @click="file.saveAs(hash, name)"
    >
      <LoaderCircle v-if="file.busy.value === 'save'" class="spin" />
      <Download v-else />
      <span v-if="!compact">Xuất…</span>
    </button>
    <BundleMenu v-if="bundleStep && file.available" :step-id="bundleStep" :name="version?.asset_name.trim() || undefined" :compact="compact" />
    <button
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact }"
      :disabled="!file.available || !!file.busy.value"
      :title="file.available ? 'Hiện trong thư mục' : offTitle"
      :aria-label="compact ? 'Hiện trong thư mục' : undefined"
      @click="file.reveal(hash, name)"
    >
      <LoaderCircle v-if="file.busy.value === 'reveal'" class="spin" />
      <FolderOpen v-else />
      <span v-if="!compact">Mở thư mục</span>
    </button>
    <button
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact, done: file.copied.value }"
      :disabled="!file.available || !!file.busy.value"
      :title="file.available ? 'Copy đường dẫn' : offTitle"
      :aria-label="compact ? 'Copy đường dẫn' : undefined"
      @click="file.copyPath(hash, name)"
    >
      <Transition name="swap" mode="out-in">
        <Check v-if="file.copied.value" key="ok" />
        <Link2 v-else key="link" />
      </Transition>
      <span v-if="!compact">{{ file.copied.value ? 'Đã copy' : 'Copy đường dẫn' }}</span>
    </button>
    <span
      v-if="file.available"
      class="drag"
      draggable="true"
      tabindex="-1"
      title="Kéo thả tệp sang Explorer, Unity, Godot…"
      @pointerenter="file.prepare(hash, name)"
      @dragstart="file.startDrag($event, hash, name)"
    >
      <GripVertical :size="14" aria-hidden="true" />
      <span v-if="!compact">Kéo ra</span>
    </span>
    <button v-if="runLink" type="button" class="btn btn-plain run" :class="{ 'btn-icon': compact }" :aria-label="compact ? 'Mở lượt chạy' : undefined" title="Mở lượt chạy" @click="router.push(`/runs/${runLink}`)">
      <ArrowUpRight />
      <span v-if="!compact">Mở lượt chạy</span>
    </button>
  </div>
</template>

<style scoped>
.asset-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.compact {
  flex-wrap: nowrap;
  gap: 2px;
}
.done {
  color: var(--st-ok);
}
.done:hover {
  color: var(--st-ok);
}
.drag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px 0 6px;
  border-radius: var(--r-sm);
  font-size: var(--fs-callout);
  font-weight: 500;
  color: var(--text-3);
  cursor: grab;
  box-shadow: inset 0 0 0 1px var(--separator);
  transition:
    background-color var(--t-fast),
    color var(--t-fast);
}
.compact .drag {
  width: 30px;
  padding: 0;
  justify-content: center;
}
.drag:hover {
  color: var(--text);
  background: var(--bg-hover);
}
.drag:active {
  cursor: grabbing;
}
.run {
  margin-left: auto;
}
.compact .run {
  margin-left: 0;
}
.spin {
  animation: spin 1s linear infinite;
}
.swap-enter-active,
.swap-leave-active {
  transition:
    opacity 120ms var(--ease-out),
    transform 160ms var(--ease-spring);
}
.swap-enter-from {
  opacity: 0;
  transform: scale(0.6);
}
.swap-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .swap-enter-active,
  .swap-leave-active {
    transition: none;
  }
}
</style>
