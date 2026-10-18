<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { X } from '@lucide/vue'
import { onKeyStroke } from '@vueuse/core'
import type { BlobRef } from '@/api/types'
import { bytes } from '@/composables/format'
import BlobImage from './BlobImage.vue'
import AssetViewer from './viewers/AssetViewer.vue'
import { VIEW_LABEL, viewsFor } from './viewers/detect'

const props = withDefaults(defineProps<{ blob: BlobRef | null; siblings?: BlobRef[] }>(), { siblings: () => [] })
const emit = defineEmits<{ close: [] }>()
const closeBtn = ref<HTMLButtonElement | null>(null)

const views = computed(() => (props.blob ? viewsFor(props.blob, props.siblings) : []))
const mode = ref(0)
const view = computed(() => views.value[mode.value] ?? views.value[0])
// A plain picture keeps the compact square sheet; real viewers get a wide stage with room for controls.
const plain = computed(() => views.value.length === 1 && view.value?.kind === 'image')

onKeyStroke('Escape', () => props.blob && emit('close'))
watch(
  () => props.blob?.hash,
  (h) => {
    mode.value = 0
    if (h) void nextTick(() => closeBtn.value?.focus())
  }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="lb">
      <div v-if="blob && view" class="scrim" role="dialog" aria-modal="true" :aria-label="blob.name ?? 'Tệp'" @click.self="emit('close')">
        <figure class="sheet" :class="{ wide: !plain }">
          <BlobImage v-if="plain" :hash="blob.hash" full class="img" />
          <div v-else class="stage">
            <Transition name="lb-swap" mode="out-in">
              <AssetViewer :key="`${view.kind}:${blob.hash}`" :view="view" />
            </Transition>
          </div>
          <figcaption class="cap">
            <span class="name">{{ blob.name ?? blob.hash.slice(0, 12) }}</span>
            <span class="caption num">
              <template v-if="blob.width">{{ blob.width }}×{{ blob.height }} · </template>{{ bytes(blob.size) }}
            </span>
            <div v-if="views.length > 1" class="seg" role="radiogroup" aria-label="Kiểu xem">
              <button
                v-for="(v, i) in views"
                :key="v.kind"
                type="button"
                role="radio"
                :aria-checked="mode === i"
                :class="{ on: mode === i }"
                @click="mode = i"
              >
                {{ VIEW_LABEL[v.kind] }}
              </button>
            </div>
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
.sheet.wide {
  width: min(1180px, 100%);
}
.img {
  width: 100%;
  border-radius: 0;
}
.stage {
  height: min(74vh, 780px);
  min-height: 320px;
  background: var(--bg-sunken);
}
.cap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px 8px 16px;
  border-top: 0.5px solid var(--separator);
}
.name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-callout);
  font-weight: 550;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    color var(--t-fast);
}
.seg button.on {
  color: var(--text);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
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
.lb-swap-enter-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.lb-swap-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.lb-swap-enter-from,
.lb-swap-leave-to {
  opacity: 0;
}
</style>
