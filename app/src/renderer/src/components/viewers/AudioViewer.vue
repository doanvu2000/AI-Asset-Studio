<script setup lang="ts">
import { computed, ref } from 'vue'
import { Repeat } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import { bytes } from '@/composables/format'

const props = defineProps<{ blob: BlobRef; wave?: BlobRef }>()

// Looping clips (music) should be heard across the seam, so the toggle defaults on for them.
const loop = ref(/loop|music|theme|bgm/i.test(props.blob.name ?? ''))
const player = ref<HTMLAudioElement | null>(null)
const progress = ref(0)
const src = computed(() => blobUrl(props.blob.hash))

function onTime(): void {
  const a = player.value
  progress.value = a && a.duration ? a.currentTime / a.duration : 0
}

function seek(e: MouseEvent): void {
  const a = player.value
  const el = e.currentTarget as HTMLElement
  if (!a || !a.duration) return
  a.currentTime = ((e.clientX - el.getBoundingClientRect().left) / el.clientWidth) * a.duration
  void a.play()
}
</script>

<template>
  <div class="vw vw-backdrop audio">
    <button v-if="wave" type="button" class="wave" aria-label="Tua tới vị trí" @click="seek">
      <img :src="blobUrl(wave.hash)" alt="" draggable="false" />
      <span class="head" :style="{ left: `${progress * 100}%` }" />
    </button>
    <div class="bar">
      <audio ref="player" :src="src" :loop="loop" controls preload="metadata" @timeupdate="onTime" />
      <label class="btn btn-plain small" :class="{ on: loop }">
        <input v-model="loop" type="checkbox" class="sr-only" />
        <Repeat :size="14" />
        Lặp
      </label>
    </div>
    <span class="msg num">{{ blob.name }} · {{ blob.mime }} · {{ bytes(blob.size) }}</span>
  </div>
</template>

<style scoped>
.audio {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 12px;
  padding: 16px;
}
.wave {
  position: relative;
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}
.wave img {
  display: block;
  width: 100%;
  height: auto;
  border-radius: 6px;
}
.head {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--accent, #fff);
  pointer-events: none;
}
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bar audio {
  flex: 1;
  min-width: 0;
}
.on {
  color: var(--accent);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
