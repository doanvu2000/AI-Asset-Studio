<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CircleAlert, Compass, LoaderCircle, Orbit, RotateCcw, SquareSplitHorizontal } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import { useReducedMotion } from '@/composables/motion'
import TweenNumber from '@/components/TweenNumber.vue'
import { createPanoScene, type PanoScene } from './panoScene'

const props = withDefaults(defineProps<{ blob: BlobRef; compact?: boolean }>(), { compact: false })

const host = ref<HTMLElement | null>(null)
const reduced = useReducedMotion()
const state = ref<'loading' | 'ready' | 'error'>('loading')
const error = ref('')
const autoRotate = ref(!reduced.value)
const yaw = ref(0)
const fov = ref(75)
const size = ref<[number, number]>([0, 0])

let scene: PanoScene | null = null
let abort: AbortController | null = null

async function load(): Promise<void> {
  dispose()
  state.value = 'loading'
  error.value = ''
  const ac = (abort = new AbortController())
  try {
    const s = await createPanoScene(host.value!, blobUrl(props.blob.hash), {
      autoRotate: autoRotate.value,
      signal: ac.signal,
      onView: (v) => {
        yaw.value = v.yaw
        fov.value = v.fov
      }
    })
    if (ac.signal.aborted) {
      s.dispose()
      return
    }
    scene = s
    host.value!.appendChild(s.canvas)
    size.value = s.size
    state.value = 'ready'
  } catch (e) {
    if (ac.signal.aborted) return
    error.value = (e as Error).message || 'Không đọc được ảnh panorama'
    state.value = 'error'
  }
}

function dispose(): void {
  abort?.abort()
  scene?.dispose()
  scene = null
}

// The wrap seam sits behind the start view (u = 0 / 1), so "seam" turns round to face it.
function look(toYaw: number): void {
  autoRotate.value = false
  scene?.lookAt(toYaw, 0, reduced.value)
}

onMounted(load)
onBeforeUnmount(dispose)
watch(() => props.blob.hash, load)
watch(autoRotate, (v) => scene?.setAutoRotate(v))

const heading = (y: number): string => {
  const names = ['Trước', 'Phải', 'Sau', 'Trái']
  return names[Math.round((((y % 360) + 360) % 360) / 90) % 4]!
}
</script>

<template>
  <div class="vw vw-backdrop pano" :class="{ compact }">
    <div ref="host" class="host vw-fade" :class="{ in: state === 'ready' }" />

    <Transition name="vw-swap">
      <div v-if="state === 'loading'" key="l" class="vw-state" role="status">
        <LoaderCircle :size="22" class="spin" />
        <span>Đang tải panorama 360°</span>
      </div>
      <div v-else-if="state === 'error'" key="e" class="vw-state err" role="alert">
        <CircleAlert :size="22" />
        <strong>Không hiển thị được panorama</strong>
        <span class="msg">{{ error }}</span>
        <button class="btn" @click="load"><RotateCcw /> Thử lại</button>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready'" class="vw-chip vw-glass num" aria-live="off">
        <span><b>360°</b></span>
        <span class="dir"><Compass :size="12" /> <b>{{ heading(yaw) }}</b> <TweenNumber :value="Math.round(yaw)" />°</span>
        <span v-if="!compact">FOV <b><TweenNumber :value="Math.round(fov)" />°</b></span>
        <span v-if="!compact && size[0]" class="dims">{{ size[0] }}×{{ size[1] }}</span>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready'" class="vw-bar vw-glass" role="toolbar" aria-label="Điều khiển panorama">
        <button class="vw-btn" :aria-pressed="autoRotate" title="Tự xoay" @click="autoRotate = !autoRotate">
          <Orbit /><span v-if="!compact">Tự xoay</span>
        </button>
        <button class="vw-btn" title="Nhìn thẳng vào mối nối trái/phải" @click="look(180)">
          <SquareSplitHorizontal /><span v-if="!compact">Mối nối</span>
        </button>
        <span class="sep" />
        <button class="vw-btn" title="Về hướng nhìn trước" aria-label="Về hướng nhìn trước" @click="look(0)"><Compass /></button>
      </div>
    </Transition>
    <p v-if="state === 'ready' && !compact" class="hint">Kéo để xoay · cuộn để phóng</p>
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
.dir {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.dims {
  color: var(--text-3);
}
.hint {
  position: absolute;
  z-index: 3;
  top: 12px;
  right: 12px;
  margin: 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
  pointer-events: none;
}
</style>
