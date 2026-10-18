<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { CircleAlert, Repeat, TriangleAlert } from '@lucide/vue'
import type { StepNodeData } from '@/composables/graph'
import { NEW_PORT } from '@/composables/graph'
import { useRuns } from '@/stores/runs'
import { kindName, STEP_LABEL, waitingLabel } from '@/composables/format'
import LaneTag from '../LaneTag.vue'
import StatusGlyph from '../StatusGlyph.vue'
import ElapsedTimer from '../ElapsedTimer.vue'
import TweenNumber from '../TweenNumber.vue'

const props = defineProps<{ data: StepNodeData }>()
const runs = useRuns()

const errors = computed(() => props.data.issues.filter((i) => i.level === 'error').length)
const warnings = computed(() => props.data.issues.length - errors.value)
const title = computed(() => {
  const n = kindName(props.data.kind)
  return n === props.data.kind ? props.data.label : n
})

const step = computed(() => props.data.step)
const progress = computed(() => (step.value ? runs.progress.get(step.value.id) : undefined))
const fraction = computed(() => {
  const p = progress.value
  if (p && p.max) return Math.min(1, p.value / p.max)
  const s = step.value
  return s?.status === 'succeeded' || s?.status === 'cached' ? 1 : null
})
const detail = computed(() => {
  const s = step.value
  if (!s) return ''
  if (s.status === 'running') return progress.value?.label || progress.value?.node || 'Đang chạy'
  if (s.status === 'waiting_resource') return waitingLabel(s.meta.waiting_for)
  if (s.status === 'failed') return s.error?.message ?? 'Lỗi'
  return STEP_LABEL[s.status]
})
</script>

<template>
  <div
    class="node"
    :class="[`lane-${data.lane}`, `m-${data.mode}`, step ? `st-${step.status}` : '', { selected: data.selected, bad: errors > 0 }]"
  >
    <span v-if="step?.status === 'running'" class="shimmer" aria-hidden="true" />
    <Handle v-if="data.mode === 'edit'" id="ref" type="target" :position="Position.Top" class="h-ref" :connectable="false" />
    <header class="head">
      <StatusGlyph v-if="step" :status="step.status" :progress="fraction" :size="22" />
      <div class="t">
        <span class="name" :title="data.kind">{{ title }}</span>
        <span class="sub">
          <span class="key">{{ data.stepId }}</span>
          <span v-if="data.mapped" class="map" :title="`Chạy lặp theo ${data.mapped}`"><Repeat :size="10" />{{ data.mapped }}</span>
        </span>
      </div>
      <LaneTag :lane="data.lane" />
    </header>

    <template v-if="data.mode === 'edit'">
      <ul v-if="data.inPorts.length || data.acceptsAny" class="ports">
        <li v-for="p in data.inPorts" :key="`i-${p}`" class="port in">
          <Handle :id="`i:${p}`" type="target" :position="Position.Left" class="h" />
          <span>{{ p }}</span>
        </li>
        <li v-if="data.acceptsAny" class="port in add">
          <Handle :id="`i:${NEW_PORT}`" type="target" :position="Position.Left" class="h h-add" />
          <span>+ nối thêm</span>
        </li>
      </ul>
      <ul v-if="data.outPorts.length" class="ports outs">
        <li v-for="p in data.outPorts" :key="`o-${p}`" class="port out">
          <span>{{ p }}</span>
          <Handle :id="`o:${p}`" type="source" :position="Position.Right" class="h" />
        </li>
      </ul>
      <div v-if="errors || warnings" class="issues">
        <span v-if="errors" class="e"><CircleAlert :size="12" /> {{ errors }} lỗi</span>
        <span v-if="warnings" class="w"><TriangleAlert :size="12" /> {{ warnings }} cảnh báo</span>
      </div>
    </template>

    <template v-else>
      <Handle id="i:in" type="target" :position="Position.Left" class="h h-run" :connectable="false" />
      <Handle id="o:out" type="source" :position="Position.Right" class="h h-run" :connectable="false" />
      <div class="run">
        <Transition name="swap" mode="out-in">
          <span :key="`${step?.status}:${progress?.label ?? ''}`" class="detail" :class="{ err: step?.status === 'failed' }">{{ detail }}</span>
        </Transition>
        <span v-if="step?.status === 'running'" class="metrics num">
          <span v-if="fraction !== null" class="pct"><TweenNumber :value="fraction * 100" />%</span>
          <ElapsedTimer v-if="step.started_at" :from="step.started_at" />
        </span>
      </div>
      <div v-if="step?.status === 'running'" class="bar" :class="{ indet: fraction === null }">
        <span class="fill" :style="{ transform: `scaleX(${fraction ?? 0.3})` }" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.node {
  --lane: var(--lane-cpu);
  position: relative;
  width: 232px;
  border-radius: 12px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-size: var(--fs-callout);
  color: var(--text);
  overflow: visible;
  transition:
    box-shadow var(--t-base) var(--ease-out),
    opacity var(--t-base) var(--ease-out),
    transform var(--t-fast) var(--ease-spring);
}
.node::before {
  content: '';
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--lane);
}
.lane-gpu {
  --lane: var(--lane-gpu);
}
.lane-blender {
  --lane: var(--lane-blender);
}
.lane-cloud {
  --lane: var(--lane-cloud);
}
.lane-none {
  --lane: var(--lane-none);
}
.node:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.node.selected {
  box-shadow: 0 0 0 2px var(--accent), var(--shadow-2);
}
.node.bad {
  box-shadow: 0 0 0 1.5px var(--st-fail), var(--shadow-1);
}
.node.bad.selected {
  box-shadow: 0 0 0 2px var(--st-fail), var(--shadow-2);
}
.st-pending,
.st-skipped {
  opacity: 0.6;
}
.st-failed {
  background: color-mix(in srgb, var(--bg-elevated) 92%, var(--st-fail));
}
.st-awaiting_human {
  background: color-mix(in srgb, var(--bg-elevated) 92%, var(--st-human));
}
.shimmer {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  overflow: hidden;
  pointer-events: none;
  background: linear-gradient(100deg, transparent 20%, var(--st-running-soft) 50%, transparent 80%);
  background-size: 200% 100%;
  animation: node-shimmer 1.8s var(--ease-out) infinite;
}
@keyframes node-shimmer {
  from {
    background-position: 150% 0;
  }
  to {
    background-position: -50% 0;
  }
}
.head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 10px 8px 14px;
}
.t {
  flex: 1;
  min-width: 0;
  display: grid;
}
.name {
  font-weight: 600;
  font-size: var(--fs-body);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.key {
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.map {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
}
.ports {
  list-style: none;
  margin: 0;
  padding: 2px 0 6px;
  border-top: 0.5px solid var(--separator);
}
.ports.outs {
  padding-top: 4px;
}
.port {
  position: relative;
  display: flex;
  align-items: center;
  height: 22px;
  padding: 0 14px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
}
.port.out {
  justify-content: flex-end;
}
.port.add {
  color: var(--text-3);
  font-family: var(--font);
  font-style: italic;
}
.issues {
  display: flex;
  gap: 10px;
  padding: 6px 14px 8px;
  border-top: 0.5px solid var(--separator);
  font-size: 11px;
  font-weight: 600;
}
.issues span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.e {
  color: var(--st-fail);
}
.w {
  color: var(--st-waiting);
}
.run {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 0 12px 10px 14px;
  min-height: 26px;
  color: var(--text-2);
}
.detail {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.detail.err {
  color: var(--st-fail);
}
.st-waiting_resource .detail {
  color: var(--st-waiting);
  font-weight: 600;
}
.st-awaiting_human .detail {
  color: var(--st-human);
  font-weight: 600;
}
.metrics {
  display: flex;
  gap: 8px;
}
.pct {
  color: var(--st-running);
  font-weight: 600;
}
.bar {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 5px;
  height: 3px;
  border-radius: 2px;
  overflow: hidden;
  background: var(--st-running-soft);
}
.fill {
  display: block;
  height: 100%;
  background: var(--st-running);
  transform-origin: left;
  transition: transform 320ms var(--ease-out);
}
.bar.indet .fill {
  animation: node-indet 1.4s var(--ease-out) infinite;
}
@keyframes node-indet {
  from {
    transform: translateX(-100%) scaleX(0.3);
  }
  to {
    transform: translateX(340%) scaleX(0.3);
  }
}
.h {
  width: 10px;
  height: 10px;
  border: 2px solid var(--bg-elevated);
  background: var(--lane);
  transition: box-shadow var(--t-fast) var(--ease-out);
}
.h:hover {
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--lane) 30%, transparent);
}
.h-add {
  background: var(--bg-elevated);
  border: 1.5px dashed var(--text-3);
}
.h-ref {
  opacity: 0;
  pointer-events: none;
}
.h-run {
  opacity: 0;
  pointer-events: none;
}
.swap-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-fast) var(--ease-out);
}
.swap-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.swap-enter-from {
  opacity: 0;
  transform: translateY(3px);
}
.swap-leave-to {
  opacity: 0;
}
</style>
