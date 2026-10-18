<script setup lang="ts">
import { computed } from 'vue'
import { Bot, Box, Power, Sparkles, Workflow } from '@lucide/vue'
import type { ResourceHealth } from '@/api/types'
import { RESOURCE_NAMES } from '@/composables/format'
import TweenNumber from './TweenNumber.vue'

const props = defineProps<{ resource: ResourceHealth; pending?: boolean }>()
const emit = defineEmits<{ toggle: [start: boolean] }>()

const icon = computed(() => ({ comfyui: Workflow, blender: Box, codex: Sparkles, claude: Bot })[props.resource.name] ?? Workflow)
const STATUS_TEXT: Record<string, string> = {
  down: 'tắt',
  starting: 'đang bật…',
  up: 'sẵn sàng',
  busy: 'đang bận',
  error: 'lỗi'
}
const name = computed(() => RESOURCE_NAMES[props.resource.name] ?? props.resource.name)
const d = computed(() => props.resource.detail)
const isOn = computed(() => ['up', 'busy', 'starting'].includes(props.resource.status))
const note = computed(() => {
  const v = d.value.message ?? d.value.error ?? d.value.warning
  return typeof v === 'string' && v ? v : null
})
const vramTotal = computed(() => Number(d.value.vram_total_mb ?? 0))
const vramFree = computed(() => Number(d.value.vram_free_mb ?? 0))
const vramUsed = computed(() => Math.max(0, vramTotal.value - vramFree.value))
const vramUsedFrac = computed(() => (vramTotal.value ? vramUsed.value / vramTotal.value : 0))
const hasVram = computed(() => d.value.vram_free_mb !== undefined && vramTotal.value > 0 && props.resource.status !== 'down')
// An attached (unmanaged) server is not ours to stop; `managed` is only meaningful while it is on.
const canToggle = computed(
  () => ['comfyui', 'blender'].includes(props.resource.name) && !(isOn.value && d.value.managed === false)
)
const tooltip = computed(() => {
  const parts = [`${name.value}: ${STATUS_TEXT[props.resource.status] ?? props.resource.status}`]
  if (d.value.version) parts.push(String(d.value.version))
  if (note.value) parts.push(note.value)
  return parts.join(' · ')
})
const gb = (mb: number): string => (mb / 1024).toFixed(1).replace('.', ',')
</script>

<template>
  <div class="res" :class="`rs-${resource.status}`" :title="tooltip">
    <component :is="icon" class="ic" :size="15" :stroke-width="2" aria-hidden="true" />
    <span class="name">{{ name }}</span>
    <span class="state">
      <span class="dot" aria-hidden="true" />
      <Transition name="fade" mode="out-in">
        <span :key="resource.status">{{ STATUS_TEXT[resource.status] ?? resource.status }}</span>
      </Transition>
    </span>
    <span v-if="hasVram" class="vram num" :title="`VRAM đã dùng ${gb(vramUsed)} / ${gb(vramTotal)} GB`">
      <span><TweenNumber :value="vramUsed" :format="gb" /> / {{ gb(vramTotal) }} GB</span>
      <span class="meter" role="meter" :aria-valuenow="Math.round(vramUsedFrac * 100)" aria-valuemin="0" aria-valuemax="100" aria-label="VRAM đã dùng">
        <span class="fill" :style="{ transform: `scaleX(${vramUsedFrac})` }" />
      </span>
    </span>
    <button
      v-if="canToggle"
      class="btn btn-plain toggle"
      :disabled="pending || resource.status === 'starting'"
      :aria-label="isOn ? `Tắt ${name}` : `Bật ${name}`"
      @click="emit('toggle', !isOn)"
    >
      <Power />
      {{ isOn ? 'Tắt' : 'Bật' }}
    </button>
  </div>
</template>

<style scoped>
.res {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  white-space: nowrap;
}
.ic {
  color: var(--text-3);
  flex: none;
  transition: color var(--t-base);
}
.rs-up .ic,
.rs-busy .ic {
  color: var(--accent);
}
.name {
  font-weight: 600;
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--st-pending);
}
.rs-up .dot {
  background: var(--st-ok);
  box-shadow: none;
}
.rs-busy .dot {
  background: var(--st-running);
  box-shadow: none;
  animation: soft-pulse 1.4s ease-in-out infinite;
}
.rs-starting .dot {
  background: var(--st-waiting);
  box-shadow: none;
  animation: soft-pulse 1s ease-in-out infinite;
}
.rs-error .dot {
  background: var(--st-fail);
  box-shadow: none;
}
.rs-error .state {
  color: var(--st-fail);
}
.vram {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.meter {
  width: 44px;
  height: 5px;
  border-radius: 3px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  overflow: hidden;
}
.fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transform-origin: left;
  transition: transform 600ms var(--ease-out);
}
.toggle {
  height: 24px;
  padding: 0 8px;
  font-size: var(--fs-callout);
}
.toggle svg {
  width: 13px;
  height: 13px;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
