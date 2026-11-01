<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { TriangleAlert } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobJson } from './viewers/fetchBlob'
import BlobThumb from './BlobThumb.vue'

interface Asset {
  rank?: number
  asset_name?: string
  blob?: BlobRef
  score?: number
  flagged?: boolean
  flags?: string[]
  metrics?: { palette_de?: number | null }
  delta_e_after?: number | null
}
interface Report {
  type?: string
  assets?: Asset[]
  skipped?: unknown[]
  warnings?: string[]
  summary?: {
    total?: number
    flagged?: number
    skipped?: number
    recolored?: number
    restyle_queued?: number
    median_palette_de?: number | null
    median_palette_de_after?: number | null
  }
}

const TYPE = 'asset-studio/style-lock-report'
const FLAG: Record<string, string> = {
  palette: 'Bảng màu',
  contrast: 'Tương phản',
  saturation: 'Độ bão hoà',
  outline: 'Viền',
  clip: 'Kiểu vẽ'
}

const props = defineProps<{ report: BlobRef }>()
const data = ref<Report | null>(null)

watch(
  () => props.report.hash,
  async (h) => {
    data.value = null
    try {
      const r = await blobJson<Report>(h)
      if (h === props.report.hash && r?.type === TYPE) data.value = r
    } catch {
      /* report unavailable (mock / deleted blob) → section stays hidden */
    }
  },
  { immediate: true }
)

const num = (x: number | null | undefined, d = 1): string => (typeof x === 'number' && Number.isFinite(x) ? x.toFixed(d) : '—')
const rows = computed(() =>
  (data.value?.assets ?? []).map((a, i) => ({
    rank: a.rank ?? i + 1,
    name: a.asset_name || '—',
    blob: a.blob && a.blob.hash ? a.blob : null,
    score: num(a.score),
    flagged: !!a.flagged,
    flags: (a.flags ?? []).map((f) => FLAG[f] ?? f),
    before: num(a.metrics?.palette_de),
    after: a.delta_e_after == null ? null : num(a.delta_e_after)
  }))
)
const s = computed(() => data.value?.summary ?? {})
const summary = computed(() =>
  [
    `${s.value.total ?? rows.value.length} asset`,
    `${s.value.flagged ?? 0} lệch`,
    s.value.skipped ? `bỏ qua ${s.value.skipped}` : '',
    s.value.recolored ? `đã chỉnh màu ${s.value.recolored}` : '',
    s.value.restyle_queued ? `chờ vẽ lại ${s.value.restyle_queued}` : '',
    s.value.median_palette_de != null
      ? `ΔE giữa ${num(s.value.median_palette_de)}${s.value.median_palette_de_after != null ? ` → ${num(s.value.median_palette_de_after)}` : ''}`
      : ''
  ]
    .filter(Boolean)
    .join(' · ')
)
const warnings = computed(() => (data.value?.warnings ?? []).filter((w) => typeof w === 'string' && w.trim() !== ''))
</script>

<template>
  <div v-if="data && rows.length" class="group">
    <h3 class="section-label">
      Xếp hạng lệch style
      <span class="caption by">· {{ summary }}</span>
    </h3>
    <p v-for="(w, i) in warnings" :key="i" class="warn caption selectable" role="status"><TriangleAlert :size="14" />{{ w }}</p>
    <div class="slr card">
      <table>
        <thead>
          <tr>
            <th class="c-rank">#</th>
            <th class="c-img"><span class="sr-only">Ảnh</span></th>
            <th>Asset</th>
            <th class="c-num">Điểm</th>
            <th>Cờ</th>
            <th class="c-num" title="Khoảng cách bảng màu tới ảnh mỏ neo (ΔE2000), trước → sau khi chỉnh màu">ΔE bảng màu</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.rank" :class="{ flagged: r.flagged }">
            <td class="c-rank num">{{ r.rank }}</td>
            <td class="c-img">
              <span v-if="r.blob" class="th checker"><BlobThumb :blob="r.blob" :size="96" /></span>
            </td>
            <td class="name selectable" :title="r.name">{{ r.name }}</td>
            <td class="c-num num">{{ r.score }}</td>
            <td>
              <span v-for="f in r.flags" :key="f" class="flag">{{ f }}</span>
              <span v-if="!r.flags.length" class="ok caption">Khớp</span>
            </td>
            <td class="c-num num">{{ r.before }}<template v-if="r.after !== null"> → {{ r.after }}</template></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.by {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
}
.slr {
  padding: 4px;
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-callout);
}
th {
  padding: 4px 8px;
  text-align: left;
  font-weight: 500;
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
}
td {
  padding: 4px 8px;
  vertical-align: middle;
  border-top: 0.5px solid var(--separator);
}
.c-rank {
  width: 2.5ch;
  color: var(--text-3);
}
.c-img {
  width: 44px;
  padding-right: 0;
}
.c-num {
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.th {
  display: block;
  width: 36px;
  height: 36px;
  border-radius: var(--r-sm);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.name {
  max-width: 18ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
tr.flagged .name {
  color: var(--st-human);
}
.flag {
  display: inline-block;
  margin: 1px 4px 1px 0;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--st-human);
  background: var(--st-human-soft);
}
.ok {
  color: var(--st-ok);
}
.warn {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
  padding: 6px 10px;
  border-radius: var(--r-md);
  color: var(--text);
  background: var(--st-waiting-soft);
  box-shadow: var(--hairline);
}
.warn svg {
  flex: none;
  color: var(--st-waiting);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
</style>
