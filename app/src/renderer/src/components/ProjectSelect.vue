<script setup lang="ts">
import { computed, watch } from 'vue'
import { FolderKanban } from '@lucide/vue'
import { NO_PROJECT } from '@/api/types'
import { useProjects } from '@/stores/projects'
import { useConnection } from '@/stores/connection'

/** Value: a project id, NO_PROJECT ("default"), or "all" when `allLabel` is given (filter mode). */
const model = defineModel<string>({ required: true })
defineProps<{ id?: string; allLabel?: string; label?: string }>()
const projects = useProjects()
const conn = useConnection()

const RETRY = '__retry'
const failed = computed(() => !!projects.error && !projects.loaded)
function retry(): void {
  if (conn.transport && !projects.loaded && !projects.loading) void projects.load().catch(() => undefined)
}
watch(() => conn.transport, retry, { immediate: true })
function onChange(e: Event): void {
  const el = e.target as HTMLSelectElement
  if (el.value !== RETRY) return void (model.value = el.value)
  el.value = model.value
  retry()
}
</script>

<template>
  <label
    class="proj-select"
    :class="{ on: model !== 'all' && model !== NO_PROJECT, err: failed }"
    :title="failed ? `Không tải được danh sách dự án: ${projects.error}` : undefined"
  >
    <FolderKanban :size="14" aria-hidden="true" class="ic" />
    <select
      :id="id"
      class="sel"
      :value="model"
      :aria-label="label ?? 'Dự án'"
      :aria-invalid="failed || undefined"
      @mousedown="failed && retry()"
      @keydown.space="failed && retry()"
      @change="onChange"
    >
      <option v-if="allLabel" value="all">{{ allLabel }}</option>
      <option :value="NO_PROJECT">Không thuộc dự án</option>
      <option v-if="failed" :value="RETRY">Không tải được dự án · Thử lại</option>
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
  height: 26px;
  max-width: 260px;
  border-radius: 6px;
  background: var(--bg-control);
  box-shadow: var(--control-shadow);
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
.proj-select.err {
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 55%, transparent);
}
.err .ic {
  color: var(--danger);
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
  font-weight: 400;
  text-overflow: ellipsis;
  appearance: auto;
  cursor: pointer;
}
.sel:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
