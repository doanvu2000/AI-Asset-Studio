<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleCheck, CircleAlert, CircleMinus } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { blobJson } from './viewers/fetchBlob'
import BlobThumb from './BlobThumb.vue'

interface Attempt {
  attempt: number
  ok?: boolean
  min_iou?: number
  iou?: Record<string, number>
  renders?: string[]
  error?: string
}
interface Report {
  attempts: Attempt[]
  min_iou: number
  checked: boolean
  passed: boolean
  skipped?: boolean
  status?: 'off' | 'passed' | 'failed' | 'skipped'
}

const props = defineProps<{ report: BlobRef }>()
const data = ref<Report | null>(null)

watch(
  () => props.report.hash,
  async (h) => {
    data.value = null
    try {
      const r = await blobJson<Report>(h)
      if (h === props.report.hash) data.value = r
    } catch {
      /* report unavailable (mock / deleted blob) → section stays hidden */
    }
  },
  { immediate: true }
)

const pct = (x: number) => `${Math.round(x * 100)}%`
const ANGLE: Record<string, string> = { '0': 'Trước', '90': 'Trái', '180': 'Sau', '270': 'Phải' }

// The core fails open when the check render breaks: the mesh is kept but was never compared, which is neither pass nor fail.
const skipped = computed(() => !!data.value && (data.value.status === 'skipped' || data.value.skipped === true))
const rows = computed(() =>
  (data.value?.checked ? data.value.attempts : []).map((a) => ({
    n: a.attempt,
    ok: !!a.ok,
    tone: a.error ? 'skip' : a.ok ? 'ok' : 'bad',
    status: a.error ? 'Bỏ qua kiểm tra (render lỗi)' : a.ok ? 'Khớp' : 'Lệch hình',
    score: a.min_iou != null ? pct(a.min_iou) : '',
    angles: Object.entries(a.iou ?? {}).map(([k, v]) => ({ k, label: ANGLE[k] ?? `${k}°`, v: pct(v) })),
    renders: (a.renders ?? []).map((hash) => ({ hash, mime: 'image/png', size: 0 }) as BlobRef)
  }))
)
</script>

<template>
  <div v-if="rows.length" class="group">
    <h3 class="section-label">
      Kiểm tra mesh
      <span class="caption by">· silhouette 4 góc so với ảnh vào, cần ≥ {{ pct(data!.min_iou) }}</span>
    </h3>
    <p v-if="skipped" class="skip-note caption" role="status">
      <CircleMinus :size="14" />
      Bỏ qua kiểm tra (render lỗi) — mesh vẫn được giữ, hãy tự xem lại trước khi duyệt.
    </p>
    <ul class="mr card">
      <li v-for="r in rows" :key="r.n" class="mr-row">
        <div class="mr-head">
          <strong>Lần {{ r.n }}</strong>
          <span class="pill" :class="r.tone">
            <CircleCheck v-if="r.tone === 'ok'" :size="13" />
            <CircleMinus v-else-if="r.tone === 'skip'" :size="13" />
            <CircleAlert v-else :size="13" />
            {{ r.status }}<template v-if="r.score"> · {{ r.score }}</template>
          </span>
        </div>
        <div v-if="r.renders.length" class="mr-strip">
          <figure v-for="(b, i) in r.renders" :key="b.hash" class="checker">
            <BlobThumb :blob="b" :size="128" />
            <figcaption v-if="r.angles[i]">{{ r.angles[i].label }} {{ r.angles[i].v }}</figcaption>
          </figure>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.by {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
}
.mr {
  margin: 0;
  padding: 4px;
  list-style: none;
  display: grid;
  gap: 2px;
}
.mr-row {
  display: grid;
  gap: 6px;
  padding: 6px;
}
.mr-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
}
.mr-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
}
.mr-strip figure {
  margin: 0;
  border-radius: var(--r-sm);
  overflow: hidden;
  box-shadow: var(--hairline);
  aspect-ratio: 1;
  position: relative;
}
.mr-strip figcaption {
  position: absolute;
  inset: auto 0 0;
  padding: 1px 4px;
  font-size: var(--fs-caption);
  color: var(--text-2);
  background: color-mix(in srgb, var(--bg-elevated) 80%, transparent);
  font-variant-numeric: tabular-nums;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px 1px 6px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.pill.ok {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.pill.skip {
  color: var(--text-2);
  background: var(--bg-active);
}
.skip-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
  padding: 6px 10px;
  border-radius: var(--r-md);
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.pill.bad {
  color: var(--st-human);
  background: var(--st-human-soft);
}
</style>
