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
  down: 'Đang tắt',
  starting: 'Đang khởi động',
  up: 'Sẵn sàng',
  busy: 'Đang bận',
  error: 'Lỗi'
}
const d = computed(() => props.resource.detail)
const vramTotal = computed(() => Number(d.value.vram_total_mb ?? 0))
const vramFree = computed(() => Number(d.value.vram_free_mb ?? 0))
const vramUsedFrac = computed(() => (vramTotal.value ? 1 - vramFree.value / vramTotal.value : 0))
const hasVram = computed(() => d.value.vram_free_mb !== undefined && props.resource.status !== 'down')
const canToggle = computed(() => d.value.managed !== false && ['comfyui', 'blender'].includes(props.resource.name))
const isOn = computed(() => ['up', 'busy', 'starting'].includes(props.resource.status))
const gb = (mb: number): string => (mb / 1024).toFixed(1).replace('.', ',')
</script>

<template>
  <article class="res card" :class="`rs-${resource.status}`">
    <header>
      <span class="ic"><component :is="icon" :size="17" :stroke-width="2" /></span>
      <div class="names">
        <h3 class="headline">{{ RESOURCE_NAMES[resource.name] ?? resource.name }}</h3>
        <Transition name="fade" mode="out-in">
          <p :key="resource.status" class="state">
            <span class="dot" />
            {{ STATUS_TEXT[resource.status] ?? resource.status }}
          </p>
        </Transition>
      </div>
      <button
        v-if="canToggle"
        class="btn btn-plain btn-icon"
        :disabled="pending || resource.status === 'starting'"
        :aria-label="isOn ? 'Tắt' : 'Bật'"
        :title="isOn ? 'Tắt' : 'Bật'"
        @click="emit('toggle', !isOn)"
      >
        <Power />
      </button>
    </header>

    <div v-if="hasVram" class="vram">
      <div class="vram-top">
        <span class="caption">VRAM trống</span>
        <span class="num val"><TweenNumber :value="vramFree" :format="gb" /> / {{ gb(vramTotal) }} GB</span>
      </div>
      <div class="meter" role="meter" :aria-valuenow="Math.round(vramUsedFrac * 100)" aria-valuemin="0" aria-valuemax="100" aria-label="VRAM đã dùng">
        <span class="fill" :style="{ transform: `scaleX(${vramUsedFrac})` }" />
      </div>
    </div>
    <p v-else class="caption meta">
      <template v-if="d.message">{{ d.message }}</template>
      <template v-else-if="d.version">{{ d.version }}</template>
      <template v-else>—</template>
    </p>
  </article>
</template>

<style scoped>
.res {
  display: grid;
  gap: 14px;
  padding: 14px 14px 16px;
  min-height: 118px;
  align-content: start;
}
header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ic {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  transition: color var(--t-base), background-color var(--t-base);
}
.rs-up .ic,
.rs-busy .ic {
  color: var(--accent);
  background: var(--accent-soft);
}
.names {
  flex: 1;
  min-width: 0;
}
.state {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--st-pending);
}
.rs-up .dot {
  background: var(--st-ok);
}
.rs-busy .dot {
  background: var(--st-running);
  animation: soft-pulse 1.4s ease-in-out infinite;
}
.rs-starting .dot {
  background: var(--st-waiting);
  animation: soft-pulse 1s ease-in-out infinite;
}
.rs-error .dot {
  background: var(--st-fail);
}
.rs-error .state {
  color: var(--st-fail);
}
.vram {
  display: grid;
  gap: 6px;
}
.vram-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.val {
  font-size: var(--fs-callout);
  font-weight: 600;
}
.meter {
  height: 6px;
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
.meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
