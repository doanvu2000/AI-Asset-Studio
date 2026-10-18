<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { CircleAlert, LoaderCircle } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { bytes } from '@/composables/format'
import { blobText } from './fetchBlob'
import { isJson } from './detect'

const props = defineProps<{ blob: BlobRef }>()
const MAX = 512 * 1024

const state = ref<'loading' | 'ready' | 'error'>('loading')
const text = ref('')
const error = ref('')

async function load(): Promise<void> {
  state.value = 'loading'
  if (props.blob.size > MAX * 4) {
    error.value = `Tệp ${bytes(props.blob.size)} quá lớn để xem trực tiếp`
    state.value = 'error'
    return
  }
  try {
    const raw = await blobText(props.blob.hash)
    let out = raw
    if (isJson(props.blob)) {
      try {
        out = JSON.stringify(JSON.parse(raw), null, 2)
      } catch {
        /* not valid JSON: show as-is */
      }
    }
    text.value = out.length > MAX ? `${out.slice(0, MAX)}\n…` : out
    state.value = 'ready'
  } catch (e) {
    error.value = (e as Error).message
    state.value = 'error'
  }
}
onMounted(load)
watch(() => props.blob.hash, load)
</script>

<template>
  <div class="vw text">
    <div v-if="state === 'loading'" class="vw-state" role="status"><LoaderCircle :size="20" class="spin" /></div>
    <div v-else-if="state === 'error'" class="vw-state err" role="alert">
      <CircleAlert :size="20" />
      <span class="msg">{{ error }}</span>
    </div>
    <pre v-else class="mono selectable">{{ text }}</pre>
  </div>
</template>

<style scoped>
.text {
  background: var(--bg-sunken);
}
pre {
  height: 100%;
  margin: 0;
  overflow: auto;
  padding: 16px 18px;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
