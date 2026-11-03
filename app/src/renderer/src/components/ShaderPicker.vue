<script setup lang="ts">
import { ref } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import type { ShaderPresetInfo } from '../../../preload/types'
import { shaderPreviewUrl, speedOk, type ShaderPicker } from '@/composables/shaderPicker'

defineProps<{ list: ShaderPresetInfo[]; picker: ShaderPicker; note: string; assignHint?: boolean }>()

const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
const live = ref('')
const broken = ref<string[]>([])

// A still frame until hover/focus, so a dozen looping thumbnails never compete with the form.
function src(picker: ShaderPicker, id: string): string {
  return shaderPreviewUrl(id, picker.tweaks.value[id], reduceMotion.value || live.value !== id)
}
</script>

<template>
  <div class="sp">
    <span class="sp-label" title="Shader viết tay: Godot 4 và Unity URP 2D (kèm material), Android Compose (AGSL, Android 13+), iOS SwiftUI (Metal, iOS 17+). Flutter không dùng">Shader</span>
    <span class="sp-note">{{ note }}</span>
    <div class="sp-chips" role="group" aria-label="Shader preset cho sprite">
      <button
        v-for="s in list"
        :key="s.id"
        type="button"
        class="sp-chip"
        :class="{ on: picker.picked.value.includes(s.id) }"
        :aria-pressed="picker.picked.value.includes(s.id)"
        :title="s.hint"
        @click="picker.toggle(s.id)"
        @pointerenter="live = s.id"
        @pointerleave="live === s.id && (live = '')"
        @focus="live = s.id"
        @blur="live === s.id && (live = '')"
      >
        <img
          v-if="src(picker, s.id) && !broken.includes(s.id)"
          class="sp-thumb"
          :src="src(picker, s.id)"
          alt=""
          draggable="false"
          @error="broken.push(s.id)"
        />
        <span class="sp-chip-label">{{ s.label }}</span>
      </button>
    </div>
    <div v-for="s in picker.pickedInfo.value" :key="s.id" class="sp-row">
      <span class="sp-name">{{ s.label }}</span>
      <input
        v-if="s.color"
        type="color"
        class="sp-color"
        :value="picker.tweaks.value[s.id]?.color ?? s.color"
        :aria-label="`Màu ${s.label}`"
        @input="picker.tweak(s.id, 'color', ($event.target as HTMLInputElement).value)"
      />
      <input
        v-if="s.speed !== null"
        class="field num sp-speed"
        :class="{ bad: !speedOk(picker.tweaks.value[s.id]?.speed) }"
        type="number"
        min="0"
        max="20"
        step="0.1"
        :value="picker.tweaks.value[s.id]?.speed ?? s.speed"
        :title="`Tốc độ (Hz), 0 = đứng yên — ${s.hint}`"
        :aria-label="`Tốc độ ${s.label} (Hz)`"
        :aria-invalid="!speedOk(picker.tweaks.value[s.id]?.speed)"
        @input="picker.tweak(s.id, 'speed', ($event.target as HTMLInputElement).valueAsNumber)"
      />
    </div>
    <p v-if="assignHint && picker.pickedInfo.value.length === 1" class="sp-note caption">Gán sẵn material vào sprite chính</p>
  </div>
</template>

<style scoped>
.sp {
  display: grid;
  gap: 6px;
}
.sp-label {
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.sp-note {
  font-size: var(--fs-caption);
  color: var(--text-3);
  margin: 0;
}
.sp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.sp-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border: 0;
  border-radius: 11px;
  background: var(--bg-sunken);
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-caption);
  transition: background-color var(--t-fast), color var(--t-fast);
}
.sp-chip:has(.sp-thumb) {
  flex-direction: column;
  justify-content: flex-end;
  gap: 2px;
  width: 84px;
  height: auto;
  padding: 4px 4px 5px;
  border-radius: var(--radius-md, 8px);
}
.sp-thumb {
  width: 56px;
  height: 56px;
  object-fit: contain;
  pointer-events: none;
}
.sp-chip-label {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sp-chip.on {
  background: var(--accent);
  color: var(--text-on-accent);
}
.sp-row {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
}
.sp-name {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.sp-color {
  width: 26px;
  height: 22px;
  padding: 0;
  border: 0;
  background: none;
}
.sp-speed {
  width: 64px;
  height: 22px;
  text-align: right;
}
.sp-speed.bad {
  box-shadow: 0 0 0 1.5px var(--danger);
}
@media (prefers-reduced-motion: reduce) {
  .sp-chip {
    transition: none;
  }
}
</style>
