<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { LogIn, LogOut } from '@lucide/vue'
import type { IoNodeData } from '@/composables/graph'
import { NEW_PORT } from '@/composables/graph'

defineProps<{ data: IoNodeData }>()

const TYPE_LABEL: Record<string, string> = {
  text: 'chữ',
  int: 'số nguyên',
  float: 'số',
  bool: 'bật/tắt',
  enum: 'lựa chọn',
  image: 'ảnh',
  mesh: 'mesh',
  preset: 'preset',
  output: ''
}
</script>

<template>
  <div class="io" :class="[data.role, { selected: data.selected, bad: data.issues.length }]">
    <header class="head">
      <component :is="data.role === 'inputs' ? LogIn : LogOut" :size="14" class="ic" />
      <span class="name">{{ data.role === 'inputs' ? 'Đầu vào recipe' : 'Đầu ra recipe' }}</span>
    </header>
    <ul class="ports">
      <li v-for="p in data.ports" :key="p.name" class="port" :class="{ off: !p.bindable }">
        <Handle v-if="data.role === 'outputs'" :id="`i:${p.name}`" type="target" :position="Position.Left" class="h" />
        <span class="pname">{{ p.name }}</span>
        <span v-if="TYPE_LABEL[p.type] ?? p.type" class="ptype">{{ TYPE_LABEL[p.type] ?? p.type }}</span>
        <Handle v-if="data.role === 'inputs' && p.bindable" :id="`o:${p.name}`" type="source" :position="Position.Right" class="h" />
      </li>
      <li v-if="data.role === 'outputs'" class="port add">
        <Handle :id="`i:${NEW_PORT}`" type="target" :position="Position.Left" class="h h-add" />
        <span>+ thêm đầu ra</span>
      </li>
      <li v-if="data.role === 'inputs' && !data.ports.length" class="port off">Chưa có đầu vào</li>
    </ul>
  </div>
</template>

<style scoped>
.io {
  width: 196px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--bg-elevated) 70%, var(--bg-sunken));
  box-shadow: var(--hairline);
  border: 1px dashed var(--separator-strong);
  font-size: var(--fs-callout);
  transition: box-shadow var(--t-base) var(--ease-out);
}
.io.selected {
  box-shadow: 0 0 0 2px var(--accent);
}
.io.bad {
  border-color: var(--st-waiting);
}
.head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 12px 7px;
  font-weight: 600;
  color: var(--text-2);
}
.ic {
  color: var(--accent);
}
.ports {
  list-style: none;
  margin: 0;
  padding: 0 0 6px;
}
.port {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 14px;
}
.inputs .port {
  justify-content: flex-end;
}
.pname {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text);
}
.ptype {
  font-size: 10px;
  color: var(--text-3);
}
.off .pname {
  color: var(--text-3);
}
.port.add,
.port.off {
  color: var(--text-3);
  font-style: italic;
}
.h {
  width: 10px;
  height: 10px;
  border: 2px solid var(--bg-elevated);
  background: var(--accent);
}
.h-add {
  background: var(--bg-elevated);
  border: 1.5px dashed var(--text-3);
}
</style>
