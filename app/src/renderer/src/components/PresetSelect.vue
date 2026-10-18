<script setup lang="ts">
import { computed, watch } from 'vue'
import { usePresets } from '@/stores/catalog'
import { useConnection } from '@/stores/connection'

const model = defineModel<string | number | boolean | undefined>()
defineProps<{ id?: string }>()
const presets = usePresets()
const conn = useConnection()

watch(
  () => conn.transport,
  (t) => t && void presets.load(),
  { immediate: true }
)

const current = computed(() => presets.list.find((p) => p.id === model.value))
</script>

<template>
  <div class="preset-select">
    <select :id="id" class="field sel" :value="String(model ?? '')" @change="model = ($event.target as HTMLSelectElement).value">
      <option v-if="!current && model" :value="String(model)">{{ model }} (không tìm thấy)</option>
      <option v-for="p in presets.list" :key="p.id" :value="p.id">{{ p.name }}</option>
    </select>
    <p v-if="current" class="caption">
      {{ current.description || current.dir_order.join(' · ') }}
      <RouterLink to="/settings" class="link">Quản lý preset</RouterLink>
    </p>
  </div>
</template>

<style scoped>
.preset-select {
  display: grid;
  gap: 6px;
}
.sel {
  max-width: 320px;
  appearance: auto;
}
.link {
  margin-left: 6px;
  color: var(--accent);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
</style>
