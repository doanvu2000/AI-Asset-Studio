<script setup lang="ts">
import { Check } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import BlobThumb from '@/components/BlobThumb.vue'

withDefaults(
  defineProps<{ blob: BlobRef; index: number; picked?: boolean; focused?: boolean; readonly?: boolean; size?: number; label: string }>(),
  { picked: false, focused: false, readonly: false, size: 384 }
)
</script>

<template>
  <button
    type="button"
    class="tile"
    role="option"
    :aria-selected="picked"
    :aria-label="label"
    :class="{ picked, focused, readonly }"
    :style="{ animationDelay: `${Math.min(index, 8) * 35}ms` }"
  >
    <BlobThumb :blob="blob" :size="size" />
    <span v-if="index < 9" class="kbd idx" aria-hidden="true">{{ index + 1 }}</span>
    <Transition name="pop">
      <span v-if="picked" class="tick" aria-hidden="true"><Check :size="14" :stroke-width="3" /></span>
    </Transition>
  </button>
</template>

<style scoped>
.tile {
  position: relative;
  padding: 0;
  border: 0;
  border-radius: var(--r-lg);
  overflow: hidden;
  background: none;
  cursor: pointer;
  box-shadow: var(--shadow-1), var(--hairline);
  transition:
    transform var(--t-base) var(--ease-spring),
    box-shadow var(--t-fast) var(--ease-out);
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
}
.tile:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2), var(--hairline);
}
.tile.focused {
  box-shadow: 0 0 0 2px var(--focus-ring), var(--shadow-2);
}
.tile.picked {
  transform: scale(0.97);
  box-shadow: 0 0 0 3px var(--accent), var(--shadow-2);
}
.tile.readonly {
  cursor: zoom-in;
}
.idx {
  position: absolute;
  top: 8px;
  left: 8px;
  background: color-mix(in srgb, var(--bg-elevated) 85%, transparent);
  backdrop-filter: blur(8px);
}
.tick {
  position: absolute;
  top: 8px;
  right: 8px;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  color: var(--text-on-accent);
  background: var(--accent);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.pop-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast);
}
.pop-leave-active {
  transition: opacity 100ms;
}
.pop-enter-from {
  transform: scale(0.3);
  opacity: 0;
}
.pop-leave-to {
  opacity: 0;
}
</style>
