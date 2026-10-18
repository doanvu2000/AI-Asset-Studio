<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleCheck, CircleMinus } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { api } from '@/stores/connection'
import BlobThumb from './BlobThumb.vue'

interface Attempt {
  attempt: number
  hash: string
  ok: boolean
  reasons?: string[]
  consistency?: { ok?: boolean; reason?: string; error?: string }
}
interface Report {
  views: { name: string; ok: boolean; attempts: Attempt[] }[]
  dropped: string[]
  consistency?: boolean
}

const props = defineProps<{ report: BlobRef }>()
const data = ref<Report | null>(null)

watch(
  () => props.report.hash,
  async (h) => {
    data.value = null
    try {
      const res = await fetch(api().blobUrl(h))
      if (res.ok && h === props.report.hash) data.value = (await res.json()) as Report
    } catch {
      /* report unavailable (mock / deleted blob) → section stays hidden */
    }
  },
  { immediate: true }
)

const VIEW_NAME: Record<string, string> = { left: 'Góc trái', back: 'Mặt sau', right: 'Góc phải' }
const REASON: Record<string, string> = {
  rotated: 'nằm ngang',
  wide: 'quá rộng',
  height: 'lệch chiều cao',
  area: 'sai cỡ',
  clipped: 'bị cắt mép',
  empty: 'trống',
  inconsistent: 'không khớp mặt trước'
}

const rows = computed(() =>
  (data.value?.views ?? []).map((v) => {
    const last = v.attempts.find((a) => a.ok) ?? v.attempts[v.attempts.length - 1]
    return {
      name: VIEW_NAME[v.name] ?? v.name,
      ok: v.ok,
      dropped: data.value!.dropped.includes(v.name),
      thumb: last ? ({ hash: last.hash, mime: 'image/png', size: 0 } as BlobRef) : null,
      tries: v.attempts.map((a) => ({
        n: a.attempt,
        ok: a.ok,
        text: a.ok ? 'đạt' : (a.reasons ?? []).map((r) => REASON[r] ?? r).join(', '),
        // Claude's own words stay in English; they explain the verdict, the chip carries the meaning.
        title: a.consistency?.reason ?? (a.consistency?.error ? `Không kiểm tra được (${a.consistency.error})` : undefined)
      }))
    }
  })
)
</script>

<template>
  <div v-if="rows.length" class="group">
    <h3 class="section-label">
      Kiểm tra góc
      <span v-if="data?.consistency" class="caption by">· hình dáng + Claude so với mặt trước</span>
    </h3>
    <ul class="vr card">
      <li v-for="r in rows" :key="r.name" class="vr-row">
        <span class="vr-thumb checker"><BlobThumb v-if="r.thumb" :blob="r.thumb" :size="128" /></span>
        <div class="vr-main">
          <div class="vr-head">
            <strong>{{ r.name }}</strong>
            <span class="pill" :class="r.ok ? 'ok' : 'drop'">
              <CircleCheck v-if="r.ok" :size="13" />
              <CircleMinus v-else :size="13" />
              {{ r.ok ? (r.tries.length > 1 ? `Đạt sau ${r.tries.length} lần` : 'Đạt') : r.dropped ? 'Đã bỏ góc' : 'Không đạt' }}
            </span>
          </div>
          <ol v-if="r.tries.length > 1 || !r.ok" class="tries">
            <li v-for="t in r.tries" :key="t.n" :class="{ ok: t.ok }" :title="t.title">
              <span class="num">{{ t.n }}</span>
              {{ t.text }}
            </li>
          </ol>
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
.vr {
  margin: 0;
  padding: 4px;
  list-style: none;
  display: grid;
  gap: 2px;
}
.vr-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 6px;
}
.vr-thumb {
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: var(--r-sm);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.vr-main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}
.vr-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px 1px 6px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
}
.pill.ok {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.pill.drop {
  color: var(--st-human);
  background: var(--st-human-soft);
}
.tries {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.tries li {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px 1px 3px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.tries li[title] {
  cursor: help;
}
.tries .num {
  display: inline-grid;
  place-items: center;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  font-size: var(--fs-caption);
  font-weight: 700;
  background: var(--bg-active);
}
.tries li.ok {
  color: var(--st-ok);
}
</style>
