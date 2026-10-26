<script setup lang="ts">
import { watch } from 'vue'
import { FolderKanban } from '@lucide/vue'
import { NO_PROJECT } from '@/api/types'
import { useProjects } from '@/stores/projects'
import { useConnection } from '@/stores/connection'

/** Value: a project id, NO_PROJECT ("default"), or "all" when `allLabel` is given (filter mode). */
const model = defineModel<string>({ required: true })
defineProps<{ id?: string; allLabel?: string; label?: string }>()
const projects = useProjects()
const conn = useConnection()

watch(
  () => conn.transport,
  (t) => t && !projects.loaded && void projects.load().catch(() => undefined),
  { immediate: true }
)
</script>

<template>
  <label class="proj-select" :class="{ on: model !== 'all' && model !== NO_PROJECT }">
    <FolderKanban :size="14" aria-hidden="true" class="ic" />
    <select :id="id" class="sel" :value="model" :aria-label="label ?? 'Dự án'" @change="model = ($event.target as HTMLSelectElement).value">
      <option v-if="allLabel" value="all">{{ allLabel }}</option>
      <option :value="NO_PROJECT">Không thuộc dự án</option>
      <option v-for="p in projects.list" :key="p.id" :value="p.id">{{ p.name }}</option>
      <option v-if="model !== 'all' && model !== NO_PROJECT && !projects.list.some((p) => p.id === model)" :value="model">
        {{ projects.byId(model)?.name ?? 'Dự án đã lưu trữ' }}
      </option>
    </select>
  </label>
</template>

<style scoped>
.proj-select {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 28px;
  max-width: 260px;
  border-radius: 7px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  transition: background-color var(--t-fast), box-shadow var(--t-fast);
}
.proj-select.on {
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent);
}
.ic {
  position: absolute;
  left: 9px;
  color: var(--text-3);
  pointer-events: none;
}
.on .ic {
  color: var(--accent);
}
.sel {
  height: 100%;
  min-width: 0;
  max-width: 260px;
  padding: 0 10px 0 29px;
  border: 0;
  border-radius: inherit;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-weight: 500;
  text-overflow: ellipsis;
  appearance: auto;
  cursor: pointer;
}
.sel:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
