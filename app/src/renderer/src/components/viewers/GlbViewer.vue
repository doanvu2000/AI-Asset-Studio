<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { usePreferredDark } from '@vueuse/core'
import { CircleAlert, Focus, Grid2x2, LoaderCircle, Orbit, RotateCcw, Shapes } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import { bytes } from '@/composables/format'
import { useReducedMotion } from '@/composables/motion'
import TweenNumber from '@/components/TweenNumber.vue'
import { createGlbScene, type GlbScene, type GlbStats } from './glbScene'

const props = withDefaults(defineProps<{ blob: BlobRef; compact?: boolean }>(), { compact: false })

const host = ref<HTMLElement | null>(null)
const dark = usePreferredDark()
const reduced = useReducedMotion()
const state = ref<'loading' | 'ready' | 'error'>('loading')
const progress = ref<number | null>(null)
const error = ref('')
const stats = shallowRef<GlbStats | null>(null)
const autoRotate = ref(!reduced.value)
const wireframe = ref(false)
const grid = ref(true)

let scene: GlbScene | null = null
let abort: AbortController | null = null

async function load(): Promise<void> {
  dispose()
  state.value = 'loading'
  progress.value = null
  error.value = ''
  const ac = (abort = new AbortController())
  try {
    const s = await createGlbScene(host.value!, blobUrl(props.blob.hash), {
      dark: dark.value,
      autoRotate: autoRotate.value,
      signal: ac.signal,
      onProgress: (f) => (progress.value = f)
    })
    if (ac.signal.aborted) {
      s.dispose()
      return
    }
    scene = s
    host.value!.appendChild(s.canvas)
    s.setWireframe(wireframe.value)
    s.setGrid(grid.value)
    stats.value = s.stats
    state.value = 'ready'
  } catch (e) {
    if (ac.signal.aborted) return
    error.value = (e as Error).message || 'Không đọc được tệp GLB'
    state.value = 'error'
  }
}

function dispose(): void {
  abort?.abort()
  scene?.dispose()
  scene = null
}

onMounted(load)
onBeforeUnmount(dispose)
watch(() => props.blob.hash, load)
watch(autoRotate, (v) => scene?.setAutoRotate(v))
watch(wireframe, (v) => scene?.setWireframe(v))
watch(grid, (v) => scene?.setGrid(v))
watch(dark, (v) => scene?.setDark(v))

const dim = (n: number): string => (n >= 10 ? n.toFixed(1) : n.toFixed(2)).replace('.', ',')
</script>

<template>
  <div class="vw vw-backdrop glb" :class="{ compact }">
    <div ref="host" class="host vw-fade" :class="{ in: state === 'ready' }" />

    <Transition name="vw-swap">
      <div v-if="state === 'loading'" key="l" class="vw-state" role="status">
        <LoaderCircle :size="22" class="spin" />
        <span class="num">
          Đang tải mô hình<template v-if="progress !== null"> · <TweenNumber :value="progress * 100" />%</template>
        </span>
      </div>
      <div v-else-if="state === 'error'" key="e" class="vw-state err" role="alert">
        <CircleAlert :size="22" />
        <strong>Không hiển thị được GLB</strong>
        <span class="msg">{{ error }}</span>
        <button class="btn" @click="load"><RotateCcw /> Thử lại</button>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready' && stats" class="vw-chip vw-glass num" aria-label="Thông số mô hình">
        <span><b><TweenNumber :value="stats.triangles" /></b> tam giác</span>
        <span><b><TweenNumber :value="stats.vertices" /></b> đỉnh</span>
        <template v-if="!compact">
          <span><b>{{ stats.materials }}</b> vật liệu</span>
          <span><b>{{ stats.textures }}</b> texture</span>
        </template>
        <span><b>{{ bytes(blob.size) }}</b></span>
        <span v-if="!compact" class="dims">{{ dim(stats.size[0]) }} × {{ dim(stats.size[1]) }} × {{ dim(stats.size[2]) }}</span>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready'" class="vw-bar vw-glass" role="toolbar" aria-label="Điều khiển 3D">
        <button class="vw-btn" :aria-pressed="autoRotate" title="Tự xoay" @click="autoRotate = !autoRotate">
          <Orbit /><span v-if="!compact">Tự xoay</span>
        </button>
        <button class="vw-btn" :aria-pressed="wireframe" title="Khung dây" @click="wireframe = !wireframe">
          <Shapes /><span v-if="!compact">Khung dây</span>
        </button>
        <button class="vw-btn" :aria-pressed="grid" title="Lưới nền" @click="grid = !grid">
          <Grid2x2 /><span v-if="!compact">Lưới</span>
        </button>
        <span class="sep" />
        <button class="vw-btn" title="Về góc nhìn ban đầu" aria-label="Về góc nhìn ban đầu" @click="scene?.resetView()"><Focus /></button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.host {
  position: absolute;
  inset: 0;
  cursor: grab;
}
.host:active {
  cursor: grabbing;
}
.host :deep(canvas) {
  width: 100% !important;
  height: 100% !important;
  outline: none;
}
.dims {
  color: var(--text-3);
}
</style>
