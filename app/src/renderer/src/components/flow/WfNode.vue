<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { CircleAlert, ImageOff, LogIn, TriangleAlert } from '@lucide/vue'
import type { WfNodeData } from '@/composables/flowGraph'
import { NODE_GLYPH, NODE_LABEL } from '@/composables/flows'
import BlobImage from '../BlobImage.vue'
import StatusGlyph from '../StatusGlyph.vue'
import TweenNumber from '../TweenNumber.vue'

const props = defineProps<{ data: WfNodeData }>()

const errors = computed(() => props.data.issues.filter((i) => i.level === 'error').length)
const warnings = computed(() => props.data.issues.length - errors.value)
const status = computed(() => props.data.status ?? 'pending')
const fraction = computed(() => (status.value === 'succeeded' ? 1 : (props.data.progress ?? null)))
const detail = computed(() => {
  if (status.value === 'failed' && props.data.error) return props.data.error
  const retry = (props.data.attempts ?? 0) > 1 && status.value === 'running' ? ` · lần ${props.data.attempts}` : ''
  return `${NODE_LABEL[status.value]}${retry}`
})
const tooltip = computed(() => `${props.data.title}\n${props.data.recipe}\nMã node: ${props.data.nodeId}`)
</script>

<template>
  <div v-if="data.mode === 'run'" class="wf run" :class="[`st-${status}`, { selected: data.selected }]">
    <Handle id="i:in" type="target" :position="Position.Top" class="h" :connectable="false" />
    <Handle id="o:out" type="source" :position="Position.Bottom" class="h" :connectable="false" />
    <header class="head">
      <StatusGlyph :status="NODE_GLYPH[status]" :progress="fraction" :size="26" />
      <span class="ord num" aria-hidden="true">{{ data.order }}</span>
      <span class="name" :title="tooltip">{{ data.title }}</span>
    </header>
    <div class="body">
      <div class="txt">
        <span class="recipe" :title="data.recipe">{{ data.recipe }}</span>
        <span class="detail" :class="{ err: status === 'failed' }" :title="detail">
          {{ detail }}
          <b v-if="status === 'running' && fraction !== null" class="pct num"><TweenNumber :value="fraction * 100" />%</b>
        </span>
      </div>
      <span v-if="data.preview" class="thumb"><BlobImage :hash="data.preview" :size="128" /></span>
      <span v-else-if="status === 'succeeded'" class="thumb empty" title="Không có ảnh xem trước"><ImageOff :size="18" /></span>
    </div>
    <div v-if="status === 'running' || status === 'awaiting_human'" class="bar" :class="{ indet: fraction === null && status === 'running' }">
      <span class="fill" :style="{ transform: `scaleX(${status === 'awaiting_human' ? 1 : (fraction ?? 0.3)})` }" />
    </div>
  </div>

  <div v-else-if="data.mode === 'inputs'" class="wf edit io" :class="{ selected: data.selected, bad: data.issues.length }">
    <header class="e-head">
      <LogIn :size="16" class="io-ic" />
      <span class="name" :title="data.title">{{ data.title }}</span>
    </header>
    <ul class="ports">
      <li v-for="p in data.inputs" :key="p.name" class="port out-only">
        <span class="pname" :title="`${p.name} · ${p.type}`">{{ p.label }}</span>
        <span class="ptype">{{ p.type }}</span>
        <Handle :id="`o:${p.name}`" type="source" :position="Position.Right" class="h" />
      </li>
      <li v-if="!data.inputs.length" class="port none">Chưa khai báo đầu vào</li>
    </ul>
  </div>

  <div v-else class="wf edit" :class="{ selected: data.selected, bad: errors > 0 }">
    <Handle id="ref" type="source" :position="Position.Right" class="h-ref" :connectable="false" />
    <header class="e-head">
      <div class="e-t">
        <span class="name" :title="tooltip">{{ data.title }}</span>
        <span v-if="data.recipe !== data.title" class="recipe" :title="data.recipe">{{ data.recipe }}</span>
      </div>
    </header>
    <div class="grid">
      <ul class="ports col-in">
        <li v-for="p in data.ins" :key="p.name" class="port" :class="{ mapped: p.mapped }">
          <Handle :id="`i:${p.name}`" type="target" :position="Position.Left" class="h" />
          <span class="pname" :title="`${p.name} · ${p.type}`">{{ p.name }}</span>
        </li>
      </ul>
      <ul class="ports col-out">
        <li v-for="o in data.outs" :key="o" class="port">
          <span class="pname" :title="o">{{ o }}</span>
          <Handle :id="`o:${o}`" type="source" :position="Position.Right" class="h" />
        </li>
      </ul>
    </div>
    <div v-if="errors || warnings" class="issues">
      <span v-if="errors" class="e"><CircleAlert :size="12" /> {{ errors }} lỗi</span>
      <span v-if="warnings" class="w"><TriangleAlert :size="12" /> {{ warnings }} cảnh báo</span>
    </div>
  </div>
</template>

<style scoped>
.wf {
  --tone: var(--st-pending);
  position: relative;
  border-radius: 14px;
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow-1), 0 0 0 1px var(--separator-strong);
  transition:
    box-shadow var(--t-base) var(--ease-out),
    background var(--t-base) var(--ease-out),
    opacity var(--t-base) var(--ease-out);
}
.wf::before {
  content: '';
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 4px;
  border-radius: 0 4px 4px 0;
  background: var(--tone);
  transition: background var(--t-base) var(--ease-out);
}
.run {
  width: 280px;
  height: 140px;
  padding: 10px 12px 14px 16px;
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 6px;
  overflow: visible;
}
.st-running {
  --tone: var(--st-running);
  animation: wf-pulse 1.8s ease-in-out infinite;
}
.st-awaiting_human {
  --tone: var(--st-human);
  background: color-mix(in srgb, var(--bg-elevated) 90%, var(--st-human));
}
.st-succeeded {
  --tone: var(--st-ok);
}
.st-failed {
  --tone: var(--st-fail);
  background: color-mix(in srgb, var(--bg-elevated) 90%, var(--st-fail));
}
.st-blocked {
  --tone: var(--st-waiting);
  opacity: 0.75;
}
.st-pending {
  opacity: 0.8;
}
.run.selected,
.edit.selected {
  box-shadow: 0 0 0 3px var(--bg-content), 0 0 0 6px var(--accent), var(--shadow-2);
}
@keyframes wf-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--st-running) 30%, transparent), var(--shadow-1);
  }
  50% {
    box-shadow: 0 0 0 2.5px color-mix(in srgb, var(--st-running) 85%, transparent), var(--shadow-1);
  }
}
@media (prefers-reduced-motion: reduce) {
  .st-running {
    animation: none;
    box-shadow: 0 0 0 2.5px var(--st-running), var(--shadow-1);
  }
}
.st-running.selected {
  animation: none;
}
.head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.ord {
  flex: none;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  display: inline-grid;
  place-items: center;
  border-radius: 11px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.name {
  flex: 1;
  min-width: 0;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.25;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.body {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-height: 0;
}
.txt {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
  align-self: stretch;
  align-content: end;
}
.recipe {
  font-size: 13px;
  color: var(--text-3);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow-wrap: anywhere;
}
.detail {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.st-running .detail,
.pct {
  color: var(--st-running);
}
.st-awaiting_human .detail {
  color: var(--st-human);
}
.st-succeeded .detail {
  color: var(--st-ok);
}
.detail.err {
  color: var(--st-fail);
}
.pct {
  margin-left: 4px;
}
.thumb {
  flex: none;
  width: 64px;
  height: 64px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  display: grid;
  place-items: center;
}
.thumb.empty {
  color: var(--text-3);
}
.bar {
  position: absolute;
  left: 16px;
  right: 12px;
  bottom: 5px;
  height: 4px;
  border-radius: 2px;
  overflow: hidden;
  background: color-mix(in srgb, var(--tone) 22%, transparent);
}
.fill {
  display: block;
  height: 100%;
  background: var(--tone);
  transform-origin: left;
  transition: transform 400ms var(--ease-out);
}
.bar.indet .fill {
  animation: wf-indet 1.4s var(--ease-out) infinite;
}
@keyframes wf-indet {
  from {
    transform: translateX(-100%) scaleX(0.3);
  }
  to {
    transform: translateX(340%) scaleX(0.3);
  }
}
.h {
  width: 12px;
  height: 12px;
  border: 2px solid var(--bg-elevated);
  background: var(--tone);
}
.edit::before {
  display: none;
}
.h-ref {
  top: 26px;
  opacity: 0;
  pointer-events: none;
}
.edit {
  width: 270px;
  --tone: var(--accent);
}
.io {
  width: 220px;
  border-style: dashed;
  background: color-mix(in srgb, var(--bg-elevated) 70%, var(--bg-sunken));
}
.edit.bad {
  box-shadow: 0 0 0 2px var(--st-fail), var(--shadow-1);
}
.e-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 50px;
  padding: 8px 12px 6px 16px;
}
.io-ic {
  color: var(--accent);
  flex: none;
}
.e-t {
  display: grid;
  min-width: 0;
  flex: 1;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding-bottom: 8px;
  border-top: 0.5px solid var(--separator);
}
.ports {
  list-style: none;
  margin: 0;
  padding: 0;
  min-width: 0;
}
.io .ports {
  padding-bottom: 8px;
  border-top: 0.5px solid var(--separator);
}
.port {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 14px;
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text-2);
}
.col-out .port {
  justify-content: flex-end;
}
.port.mapped {
  color: var(--accent);
  font-weight: 600;
}
.out-only {
  justify-content: space-between;
}
.none {
  font-family: var(--font);
  color: var(--text-3);
  font-style: italic;
}
.pname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ptype {
  font-size: 11px;
  color: var(--text-3);
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
</style>
