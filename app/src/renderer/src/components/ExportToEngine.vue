<script setup lang="ts">
import { computed, ref } from 'vue'
import { Package } from '@lucide/vue'
import PackDialog from './PackDialog.vue'
import { useProjects } from '@/stores/projects'

const props = defineProps<{ projectId: string; primary?: boolean }>()
const projects = useProjects()
const open = ref(false)

const target = computed(() => {
  const t = projects.byId(props.projectId)?.engine_target
  return t === 'unity' || t === 'godot' ? t : undefined
})
const NAME = { unity: 'Unity', godot: 'Godot' }
</script>

<template>
  <button
    type="button"
    class="btn"
    :class="{ 'btn-primary': primary }"
    :title="target ? `Đóng gói asset đã duyệt cho ${NAME[target]}` : 'Đóng gói asset đã duyệt thành Unity package / Godot addon'"
    @click="open = true"
  >
    <Package /> <span class="lbl">Xuất ra {{ target ? NAME[target] : 'engine' }}</span>
  </button>
  <PackDialog :open="open" :project-id="projectId" :engine="target" @close="open = false" />
</template>
