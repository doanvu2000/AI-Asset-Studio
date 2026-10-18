<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CircleAlert, LoaderCircle, Pause, Play, RotateCcw, SquareDashed } from '@lucide/vue'
import { SpinePlayer } from '@esotericsoftware/spine-player'
import '@esotericsoftware/spine-player/dist/spine-player.css'
import { blobUrl } from '@/stores/connection'
import { useReducedMotion } from '@/composables/motion'
import { blobText, toDataUri } from './fetchBlob'
import type { SpineSet } from './detect'

// Must match PLAYER_VERSION in core/studio_core/ops/spine.py (the exported JSON targets this runtime).
const RUNTIME = '4.2.43'

const props = withDefaults(defineProps<{ spine: SpineSet; compact?: boolean }>(), { compact: false })

interface SkeletonDoc {
  skeleton?: { spine?: string }
  bones?: unknown[]
  slots?: unknown[]
  animations?: Record<string, unknown>
}

const reduced = useReducedMotion()
const host = ref<HTMLElement | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const error = ref('')
const animations = ref<string[]>([])
const animation = ref('')
const playing = ref(!reduced.value)
const checker = ref(false)
const info = ref({ bones: 0, slots: 0, version: '' })

let player: SpinePlayer | null = null
let textureUrl = ''
let token = 0

function pageNames(atlas: string): string[] {
  const lines = atlas.split(/\r?\n/)
  return lines.filter((l, i) => l.trim() && !l.includes(':') && (i === 0 || !lines[i - 1]!.trim()))
}

async function load(): Promise<void> {
  dispose()
  const my = ++token
  state.value = 'loading'
  error.value = ''
  try {
    const [skText, atlasText, texture] = await Promise.all([
      blobText(props.spine.skeleton.hash),
      blobText(props.spine.atlas.hash),
      fetch(blobUrl(props.spine.texture.hash)).then((r) => {
        if (!r.ok) throw new Error(`Không tải được texture (HTTP ${r.status})`)
        return r.blob()
      })
    ])
    if (my !== token) return
    const doc = JSON.parse(skText) as SkeletonDoc
    const names = Object.keys(doc.animations ?? {})
    animations.value = names
    animation.value = names.includes('idle') ? 'idle' : (names[0] ?? '')
    info.value = { bones: doc.bones?.length ?? 0, slots: doc.slots?.length ?? 0, version: doc.skeleton?.spine ?? '' }
    const pages = pageNames(atlasText)
    if (pages.length > 1) throw new Error(`Atlas có ${pages.length} trang texture; trình xem chỉ hỗ trợ 1 trang`)
    textureUrl = URL.createObjectURL(texture)
    const raw: Record<string, string> = {
      'skeleton.json': toDataUri(skText, 'application/json'),
      'skeleton.atlas': toDataUri(atlasText, 'text/plain')
    }
    if (pages[0]) raw[pages[0]] = textureUrl
    player = new SpinePlayer(host.value!, {
      jsonUrl: 'skeleton.json',
      atlasUrl: 'skeleton.atlas',
      rawDataURIs: raw,
      animation: animation.value || undefined,
      showControls: false,
      showLoading: false,
      alpha: true,
      backgroundColor: '#00000000',
      premultipliedAlpha: /^pma:\s*true/m.test(atlasText),
      preserveDrawingBuffer: false,
      viewport: { padLeft: '10%', padRight: '10%', padTop: '8%', padBottom: '8%', transitionTime: 0.3 },
      success: (p) => {
        if (my !== token) return
        if (!playing.value) p.pause()
        state.value = 'ready'
      },
      error: (_p, msg) => {
        if (my !== token) return
        error.value = msg
        state.value = 'error'
      }
    })
  } catch (e) {
    if (my !== token) return
    error.value = (e as Error).message
    state.value = 'error'
  }
}

function dispose(): void {
  token += 1
  if (player) {
    const gl = player.context?.gl
    try {
      player.dispose()
    } catch {
      /* already detached */
    }
    gl?.getExtension('WEBGL_lose_context')?.loseContext()
    player = null
  }
  if (textureUrl) URL.revokeObjectURL(textureUrl)
  textureUrl = ''
}

onMounted(load)
onBeforeUnmount(dispose)
watch(() => [props.spine.skeleton.hash, props.spine.atlas.hash, props.spine.texture.hash], load)

function choose(name: string): void {
  animation.value = name
  if (!player?.skeleton) return
  player.setAnimation(name, true)
  if (!playing.value) {
    playing.value = true
    player.play()
  }
}
watch(playing, (p) => (p ? player?.play() : player?.pause()))
</script>

<template>
  <div class="vw spine" :class="[{ compact }, checker ? 'checker' : 'vw-backdrop']">
    <div ref="host" class="host vw-fade" :class="{ in: state === 'ready' }" />

    <Transition name="vw-swap">
      <div v-if="state === 'loading'" class="vw-state" role="status">
        <LoaderCircle :size="22" class="spin" />
        <span>Đang dựng skeleton Spine</span>
      </div>
      <div v-else-if="state === 'error'" class="vw-state err" role="alert">
        <CircleAlert :size="22" />
        <strong>Không hiển thị được Spine</strong>
        <span class="msg">{{ error }}</span>
        <button class="btn" @click="load"><RotateCcw /> Thử lại</button>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready'" class="vw-chip vw-glass num">
        <span><b>{{ info.bones }}</b> xương</span>
        <span><b>{{ info.slots }}</b> slot</span>
        <span v-if="!compact && info.version">Spine <b>{{ info.version }}</b></span>
      </div>
    </Transition>

    <Transition name="vw-rise">
      <div v-if="state === 'ready'" class="vw-bar vw-glass" role="toolbar" aria-label="Điều khiển Spine">
        <button class="vw-btn" :aria-label="playing ? 'Tạm dừng' : 'Phát'" :title="playing ? 'Tạm dừng' : 'Phát'" @click="playing = !playing">
          <Pause v-if="playing" /><Play v-else />
        </button>
        <template v-if="animations.length">
          <span class="sep" />
          <div class="anims" role="radiogroup" aria-label="Hoạt ảnh">
            <button
              v-for="a in animations"
              :key="a"
              class="vw-btn"
              role="radio"
              :aria-checked="animation === a"
              :aria-pressed="animation === a"
              @click="choose(a)"
            >
              {{ a }}
            </button>
          </div>
        </template>
        <span class="sep" />
        <button class="vw-btn" :aria-pressed="checker" title="Nền ô kẻ (trong suốt)" aria-label="Nền ô kẻ" @click="checker = !checker"><SquareDashed /></button>
      </div>
    </Transition>

    <p class="license" :title="`spine-player ${RUNTIME}. Spine Runtimes License: mỗi người dùng sản phẩm cần giấy phép Spine Editor riêng.`">
      Spine Runtimes {{ RUNTIME }} · dùng thương mại cần giấy phép Spine Editor
    </p>
  </div>
</template>

<style scoped>
.host {
  position: absolute;
  inset: 0 0 64px;
}
.host :deep(.spine-player) {
  background: transparent !important;
}
.host :deep(.spine-player-canvas) {
  outline: none;
}
.anims {
  display: flex;
  gap: 2px;
}
.vw-bar {
  bottom: 26px;
}
.license {
  position: absolute;
  z-index: 3;
  left: 12px;
  right: 12px;
  bottom: 6px;
  font-size: 10px;
  color: var(--text-3);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
