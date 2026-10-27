<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleAlert, CircleCheck, Grid2x2, Grid3x3, ScanLine, Shuffle, Square, TriangleAlert } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobUrl } from '@/stores/connection'
import TweenNumber from '@/components/TweenNumber.vue'
import { blobJson } from './fetchBlob'
import { wangIndex } from './detect'

interface TileReport {
  score?: number
  seam_score?: number
  passed?: boolean
  min_score?: number
  textures?: { hash?: string; score?: number; seam_score?: number; issues?: string[] }[]
}

const props = withDefaults(
  defineProps<{
    blob: BlobRef
    tiles?: BlobRef[]
    report?: BlobRef
    normal?: BlobRef
    pixel?: BlobRef
    normals?: (BlobRef | undefined)[]
    pixels?: (BlobRef | undefined)[]
    compact?: boolean
    wang?: boolean
  }>(),
  { tiles: () => [], report: undefined, normal: undefined, pixel: undefined, normals: () => [], pixels: () => [], compact: false, wang: false }
)

type MapKind = 'color' | 'normal' | 'pixel'
const REPEATS = [1, 3, 5] as const
const repeat = ref<(typeof REPEATS)[number]>(props.wang ? 5 : 3)
const map = ref<MapKind>('color')
const mix = ref(true)
const seams = ref(false)
watch(
  () => props.wang,
  (w) => (repeat.value = w ? 5 : 3)
)

const variants = computed(() => (props.tiles.length ? props.tiles : [props.blob]))
const shown = computed<BlobRef[]>(() => {
  const per = map.value === 'normal' ? props.normals : map.value === 'pixel' ? props.pixels : null
  const one = map.value === 'normal' ? props.normal : map.value === 'pixel' ? props.pixel : undefined
  if (!per) return variants.value
  // Wang cells are looked up by position, so the list must stay index-aligned: a missing map shows the colour tile.
  if (props.wang) return variants.value.map((t, i) => per[i] ?? t)
  const list = variants.value.map((_, i) => per[i] ?? (i === 0 ? one : undefined)).filter((b): b is BlobRef => !!b)
  return list.length ? list : one ? [one] : variants.value
})
// Tiles keep their own aspect; a square box with % background-size would stretch a non-square texture.
const aspect = computed(() => {
  const b = shown.value[0]
  return b?.width && b?.height ? b.width / b.height : 1
})
const mixing = computed(() => props.wang || (mix.value && shown.value.length > 1))
const crisp = computed(() => map.value === 'pixel' || Math.max(shown.value[0]?.width ?? 999, shown.value[0]?.height ?? 999) <= 128)

const hash = (x: number, y: number, seed = 0): number => Math.imul((x * 73856093) ^ (y * 19349663) ^ (seed * 83492791), 2654435761) >>> 0
// Wang: a random a/b label per grid corner, each cell the tile whose corners match, so the preview is always valid.
const wangSeed = ref(1)
const wangPos = computed(() => new Map(variants.value.map((t, i) => [wangIndex(t), i])))
const wangCell = (x: number, y: number): BlobRef => {
  const c = (cx: number, cy: number): number => (hash(cx, cy, wangSeed.value) >>> 7) & 1
  const k = (c(x, y) << 3) | (c(x + 1, y) << 2) | (c(x, y + 1) << 1) | c(x + 1, y + 1)
  return shown.value[wangPos.value.get(k) ?? 0] ?? shown.value[0]!
}
// Fixed pseudo-random pick per cell, so toggling the grid size does not reshuffle the cells already on screen.
const cells = computed(() =>
  Array.from({ length: repeat.value * repeat.value }, (_, i) => {
    const x = i % repeat.value
    const y = Math.floor(i / repeat.value)
    return props.wang ? wangCell(x, y) : shown.value[hash(x, y) % shown.value.length]!
  })
)
const fieldStyle = computed(() => ({
  backgroundImage: mixing.value ? undefined : `url("${blobUrl(shown.value[0]!.hash)}")`,
  backgroundSize: `${100 / repeat.value}% ${100 / repeat.value}%`,
  '--ar': String(aspect.value),
  '--n': String(repeat.value)
}))

const report = ref<TileReport | null>(null)
const reportErr = ref(false)
watch(
  () => props.report?.hash,
  async (h) => {
    report.value = null
    reportErr.value = false
    if (!h) return
    try {
      report.value = await blobJson<TileReport>(h)
    } catch {
      reportErr.value = true
    }
  },
  { immediate: true }
)
const ISSUE_LABEL: Record<string, string> = {
  seam: 'Lộ mối nối ở mép',
  gradient: 'Sáng tối lệch một phía',
  repetition: 'Có mảng lớn dễ thấy lặp',
  duplicate: 'Ảnh lặp lại chính nó trong một ô',
  self_repeat: 'Hoạ tiết lớn tự lặp lại (lát sẽ lộ)'
}
const entry = computed(() => report.value?.textures?.find((t) => t.hash === props.blob.hash) ?? null)
const score = computed(() => entry.value?.score ?? report.value?.score ?? null)
const issues = computed(() => (entry.value?.issues ?? report.value?.textures?.flatMap((t) => t.issues ?? []) ?? []).filter((v, i, a) => a.indexOf(v) === i))
const tone = computed(() => (score.value === null ? '' : score.value >= 85 ? 'good' : score.value >= (report.value?.min_score ?? 70) ? 'ok' : 'bad'))
</script>

<template>
  <div class="vw tile vw-backdrop" :class="{ compact }">
    <div class="stage"><div class="fit">
      <div
        class="tfield"
        :class="{ crisp, seams, mixing }"
        :style="fieldStyle"
        role="img"
        :aria-label="`Texture lát ${repeat}×${repeat}`"
      >
        <template v-if="mixing">
          <img v-for="(c, i) in cells" :key="`${repeat}-${i}`" :src="blobUrl(c.hash)" alt="" draggable="false" decoding="async" />
        </template>
      </div>
    </div></div>

    <div class="vw-chip vw-glass num">
      <span v-if="blob.width"><b>{{ blob.width }}×{{ blob.height }}</b> px</span>
      <span v-if="wang"><b>16</b> ô Wang</span>
      <span v-else-if="variants.length > 1"><b>{{ variants.length }}</b> biến thể</span>
      <span v-if="score !== null" class="score" :class="tone" :title="report?.seam_score != null ? `Mối nối: ${report.seam_score}` : undefined">
        <CircleCheck v-if="tone === 'good'" :size="13" />
        <TriangleAlert v-else :size="13" />
        Liền mạch <b><TweenNumber :value="score" /></b>/100
      </span>
      <span v-else-if="reportErr" class="score bad"><CircleAlert :size="13" /> Không đọc được điểm</span>
      <span v-for="k in compact ? [] : issues" :key="k" class="issue">{{ ISSUE_LABEL[k] ?? k }}</span>
    </div>

    <div class="vw-bar vw-glass" role="toolbar" aria-label="Điều khiển xem lát">
      <button
        v-for="n in REPEATS"
        :key="n"
        class="vw-btn num"
        :aria-pressed="repeat === n"
        :title="`Lát ${n}×${n}`"
        :aria-label="`Lát ${n}×${n}`"
        @click="repeat = n"
      >
        <Square v-if="n === 1" /><Grid2x2 v-else-if="n === 3" /><Grid3x3 v-else />
        <span v-if="!compact">{{ n }}×{{ n }}</span>
      </button>
      <span class="sep" />
      <button class="vw-btn" :aria-pressed="seams" title="Hiện ranh giới ô để soi mối nối" aria-label="Ranh giới ô" @click="seams = !seams">
        <ScanLine /><span v-if="!compact">Ranh giới</span>
      </button>
      <button v-if="wang" class="vw-btn" title="Xếp ngẫu nhiên kiểu khác (luôn khớp nhãn góc)" aria-label="Xếp lại ngẫu nhiên" @click="wangSeed++">
        <Shuffle /><span v-if="!compact">Xếp lại</span>
      </button>
      <button v-else-if="variants.length > 1" class="vw-btn" :aria-pressed="mix" title="Trộn các biến thể khi lát" aria-label="Trộn biến thể" @click="mix = !mix">
        <Shuffle /><span v-if="!compact">Trộn</span>
      </button>
      <template v-if="normal || pixel">
        <span class="sep" />
        <button class="vw-btn" :aria-pressed="map === 'color'" @click="map = 'color'">Màu</button>
        <button v-if="normal" class="vw-btn" :aria-pressed="map === 'normal'" title="Normal map" @click="map = 'normal'">Normal</button>
        <button v-if="pixel" class="vw-btn" :aria-pressed="map === 'pixel'" title="Bản pixel art" @click="map = 'pixel'">Pixel</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.stage {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 56px 16px 60px;
}
.compact .stage {
  padding: 44px 8px 52px;
}
.fit {
  width: 100%;
  height: 100%;
  container-type: size;
  display: grid;
  place-items: center;
}
.tfield {
  position: relative;
  width: min(100cqw, calc(100cqh * var(--ar, 1)));
  aspect-ratio: var(--ar, 1);
  border-radius: 6px;
  background-repeat: repeat;
  box-shadow: var(--shadow-1), var(--hairline);
  overflow: hidden;
  transition: background-size var(--t-slow) var(--ease-out);
}
.tfield.crisp,
.tfield.crisp img {
  image-rendering: pixelated;
}
.tfield.mixing {
  display: grid;
  grid-template-columns: repeat(var(--n), minmax(0, 1fr));
  grid-template-rows: repeat(var(--n), minmax(0, 1fr));
}
.tfield.mixing img {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: block;
  object-fit: fill;
}
/* Boundary lines sit over the tile edges, where a bad seam would show. */
.tfield.seams::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(to right, color-mix(in srgb, var(--accent) 70%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in srgb, var(--accent) 70%, transparent) 1px, transparent 1px);
  background-size: calc(100% / var(--n)) calc(100% / var(--n));
  background-position: -0.5px -0.5px;
}
.score {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.score.good {
  color: var(--st-ok);
}
.score.ok {
  color: var(--text);
}
.score.bad {
  color: var(--st-fail);
}
.issue {
  color: var(--text-3);
}
</style>
