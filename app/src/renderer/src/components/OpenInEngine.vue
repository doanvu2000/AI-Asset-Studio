<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Gamepad2, LoaderCircle } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import type { PreviewEngine, PreviewEngines } from '@/api/enginePreview'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

// Shown only for a step that carries a playable scene (op.playable_scene, or the gate that passes it on).
const props = defineProps<{ stepId: string; outputs?: Record<string, BlobRef[]> }>()

const SCENE_FILE = 'playable_scene.json'
const LABEL: Record<PreviewEngine, string> = { godot: 'Godot', unity: 'Unity' }
const toasts = useToasts()
const engines = ref<PreviewEngines['engines'] | null>(null)
const busy = ref<PreviewEngine | null>(null)

const playable = computed(() => Object.values(props.outputs ?? {}).some((refs) => refs.some((r) => r.name === SCENE_FILE)))

watch(
  playable,
  async (on) => {
    if (!on || engines.value) return
    try {
      engines.value = (await api().enginePreviewEngines()).engines
    } catch {
      engines.value = null
    }
  },
  { immediate: true }
)

function reason(e: PreviewEngine): string {
  const info = engines.value?.[e]
  if (!info) return `Mở màn chơi thử trong ${LABEL[e]}`
  return info.available ? `Xuất rồi mở màn chơi thử trong editor ${LABEL[e]}` : (info.reason ?? `Chưa có ${LABEL[e]}`)
}

async function open(e: PreviewEngine): Promise<void> {
  busy.value = e
  try {
    const res = await api().openInEngine(props.stepId, e)
    toasts.push(`${LABEL[e]}: ${res.note}`, 'info', 6000)
  } catch (err) {
    toasts.error(err, `Không mở được trong ${LABEL[e]}`)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div v-if="playable" class="open-engine" role="group" aria-label="Mở màn chơi thử">
    <button
      v-for="e in (['godot', 'unity'] as PreviewEngine[])"
      :key="e"
      type="button"
      class="btn"
      :disabled="!!busy || (!!engines && !engines[e].available)"
      :title="reason(e)"
      @click="open(e)"
    >
      <LoaderCircle v-if="busy === e" class="spin" />
      <Gamepad2 v-else />
      <span>Mở trong {{ LABEL[e] }}</span>
    </button>
  </div>
</template>

<style scoped>
.open-engine {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
